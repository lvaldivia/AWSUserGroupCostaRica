"""
Crea y puebla la tabla DynamoDB para el demo Shadow Agent.
Uso: python setup_dynamodb.py
"""
import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
PROFILE = "bedrock-demo"
TABLE_NAME = "MonarcaStoreOrders"

session = boto3.Session(profile_name=PROFILE)
dynamodb = session.resource("dynamodb", region_name=REGION)
client = session.client("dynamodb", region_name=REGION)

ORDERS = [
    {"order_id": "1001", "product": "Laptop Gaming MSI", "status": "enviada",
     "eta": "llega mañana", "customer": "Carlos"},
    {"order_id": "1002", "product": "Auriculares Sony WH-1000XM5", "status": "en preparación",
     "eta": "pendiente", "customer": "María"},
    {"order_id": "1003", "product": "Teclado mecánico Keychron", "status": "entregada",
     "eta": "entregada el 28 mayo", "customer": "José"},
    {"order_id": "1004", "product": "Monitor LG 4K", "status": "cancelada",
     "eta": "cancelada por el cliente", "customer": "Ana"},
]


def create_table():
    try:
        client.describe_table(TableName=TABLE_NAME)
        print(f"✅ La tabla '{TABLE_NAME}' ya existe.")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    print(f"Creando tabla '{TABLE_NAME}'...")
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "order_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "order_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    print("✅ Tabla creada.")


def seed_data():
    table = dynamodb.Table(TABLE_NAME)
    with table.batch_writer() as batch:
        for order in ORDERS:
            batch.put_item(Item=order)
    print(f"✅ {len(ORDERS)} órdenes cargadas en '{TABLE_NAME}'.")


if __name__ == "__main__":
    create_table()
    seed_data()
