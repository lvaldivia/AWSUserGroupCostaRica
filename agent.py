import boto3
import json
import urllib.request
from decimal import Decimal
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = "us-east-1"
PROFILE = "bedrock-demo"
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TABLE_NAME = "MonarcaStoreOrders"
MAX_HISTORY_MESSAGES = 20  # cap conversation history to avoid unbounded token growth

# Load AWS credentials from the local "bedrock-demo" profile (mounted read-only in Docker)
session = boto3.Session(profile_name=PROFILE)

# Bedrock client with a short connect timeout and limited retries, so a network
# hiccup fails fast instead of hanging during a live demo
bedrock = session.client(
    "bedrock-runtime",
    region_name=REGION,
    config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
)

# DynamoDB resource + table handle used by the get_order tool
dynamodb = session.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

SYSTEM_PROMPT = """
Eres Shadow Agent, el asistente de soporte de MonarcaStore.

Tienes dos herramientas disponibles:
- get_order: consulta el estado real de una orden en la base de datos por su número.
- get_exchange_rate: consulta el tipo de cambio actual entre dos monedas.

Reglas:
- Nunca inventes datos de órdenes ni tipos de cambio. Siempre usa la herramienta correspondiente.
- Si el cliente menciona un número de orden, llama a get_order.
- Si el cliente pregunta por precios en otra moneda o tipo de cambio, llama a get_exchange_rate.
- Responde de forma amigable, clara y en español.
- Si la orden no existe, indícalo con empatía.
- Siempre ofrece ayuda adicional al final de tu respuesta.
"""

# Tool definitions in Bedrock Converse API format (JSON Schema per tool input)
TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "get_order",
                "description": "Consulta el estado de una orden por su número en la base de datos de MonarcaStore.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "order_id": {
                                "type": "string",
                                "description": "Número de orden, ej. '1001'",
                            }
                        },
                        "required": ["order_id"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "get_exchange_rate",
                "description": "Consulta el tipo de cambio actual entre dos monedas, ej. USD a PEN.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "from_currency": {"type": "string", "description": "Moneda origen, ej. USD"},
                            "to_currency": {"type": "string", "description": "Moneda destino, ej. PEN"},
                        },
                        "required": ["from_currency", "to_currency"],
                    }
                },
            }
        },
    ]
}

# Friendly label per tool, shown to the user while the tool call is in flight
TOOL_LOADING_MESSAGES = {
    "get_order": "🔍 Buscando tu orden, espera un momento...",
    "get_exchange_rate": "💱 Consultando el tipo de cambio, espera un momento...",
}


def _decimal_to_native(obj):
    # DynamoDB returns numbers as Decimal; convert recursively to int/float
    # so they can be safely JSON-serialized when sent back to the model
    if isinstance(obj, list):
        return [_decimal_to_native(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def get_order(order_id: str) -> dict:
    # Tool implementation: fetch a single order by its partition key
    try:
        resp = table.get_item(Key={"order_id": str(order_id)})
    except ClientError as e:
        return {"error": f"Error consultando DynamoDB: {e.response['Error']['Code']}"}

    item = resp.get("Item")
    if not item:
        return {"error": f"No se encontró la orden #{order_id}"}
    return _decimal_to_native(item)


def get_exchange_rate(from_currency: str, to_currency: str) -> dict:
    # Tool implementation: call a free, no-API-key exchange rate service
    from_currency = (from_currency or "").upper().strip()
    to_currency = (to_currency or "").upper().strip()
    url = f"https://open.er-api.com/v6/latest/{from_currency}"

    try:
        # Short timeout so a slow external API doesn't stall the whole demo
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"error": f"No se pudo consultar el tipo de cambio: {e}"}

    if data.get("result") != "success":
        return {"error": "La API de tipo de cambio no devolvió un resultado válido."}

    rate = data.get("rates", {}).get(to_currency)
    if rate is None:
        return {"error": f"No se encontró la moneda {to_currency}"}

    return {
        "from": from_currency,
        "to": to_currency,
        "rate": rate,
        "updated_utc": data.get("time_last_update_utc"),
    }


# Dispatch table: maps a tool name (as requested by the model) to the actual Python function
TOOL_FUNCTIONS = {
    "get_order": lambda inp: get_order(inp.get("order_id", "")),
    "get_exchange_rate": lambda inp: get_exchange_rate(
        inp.get("from_currency", ""), inp.get("to_currency", "")
    ),
}


def _trim_history(history: list) -> None:
    # Keep only the most recent messages once the conversation gets long
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[: len(history) - MAX_HISTORY_MESSAGES]


def chat(mensaje: str, history: list) -> str:
    # Append the new user turn to the running conversation history
    history.append({"role": "user", "content": [{"text": mensaje}]})

    # Loop until the model stops requesting tools and returns final text
    while True:
        try:
            # Ask Claude (via Bedrock Converse) for the next step: either
            # a text reply or a request to call one of the defined tools
            response = bedrock.converse(
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=history,
                toolConfig=TOOL_CONFIG,
                inferenceConfig={"maxTokens": 500},
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"]["Message"]
            if code == "AccessDeniedException":
                return "⚠️ Sin permisos para llamar al modelo. Revisa las policies del usuario IAM."
            if code == "ThrottlingException":
                return "⚠️ Demasiadas solicitudes seguidas. Intenta de nuevo en un momento."
            return f"⚠️ Error de Bedrock ({code}): {msg}"
        except Exception as e:
            return f"⚠️ Error inesperado: {e}"

        # Save the model's turn (text and/or tool_use blocks) into history
        output_message = response["output"]["message"]
        history.append(output_message)
        stop_reason = response["stopReason"]

        if stop_reason != "tool_use":
            # Model is done: extract and return the final text blocks
            texts = [c["text"] for c in output_message["content"] if "text" in c]
            _trim_history(history)
            return "\n".join(texts).strip()

        # Model requested one or more tool calls in this turn
        tool_results = []
        for block in output_message["content"]:
            if "toolUse" not in block:
                continue  # skip non-tool blocks (e.g. any accompanying text)

            tool_use = block["toolUse"]
            name = tool_use["name"]
            tool_input = tool_use.get("input", {})
            tool_use_id = tool_use["toolUseId"]

            # Show a friendly "loading" message so the user knows something
            # is happening while we hit DynamoDB or the exchange rate API
            loading_msg = TOOL_LOADING_MESSAGES.get(name, "⏳ Procesando, espera un momento...")
            print(f"\n{loading_msg}")

            func = TOOL_FUNCTIONS.get(name)
            result = func(tool_input) if func else {"error": f"Herramienta desconocida: {name}"}

            # Package the tool's output back in the format Bedrock expects
            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"json": result}],
                    }
                }
            )

        # Feed all tool results back as a "user" turn and loop again so the
        # model can incorporate them into its next response
        history.append({"role": "user", "content": tool_results})


def main():
    # Simple CLI banner for the live demo
    print("=" * 50)
    print("  Shadow Agent — MonarcaStore Support")
    print("  Powered by Amazon Bedrock + Claude")
    print("  Herramientas: DynamoDB (órdenes) + tipo de cambio en vivo")
    print("=" * 50)
    print("Escribe 'salir' para terminar.\n")

    history = []  # conversation state, persists across turns in this session

    while True:
        user_input = input("Tú: ").strip()
        if user_input.lower() in ["salir", "exit", "quit"]:
            print("Shadow Agent: ¡Hasta luego! Que la sombra te acompañe. 👋")
            break
        if not user_input:
            continue  # ignore empty input, re-prompt
        print("\nShadow Agent: ", end="", flush=True)
        response = chat(user_input, history)
        print(response)
        print()


if __name__ == "__main__":
    main()