# Adyar Wokwi HTTPS Relay

This separate FastAPI service accepts authenticated telemetry from the public
Adyar Wokwi simulation and republishes it to EMQX Cloud. It does not modify or
replace the existing CivicTwin-R backend.

## Environment variables

Required:

```text
WOKWI_ADYAR_INGEST_TOKEN=<long-random-token>
RELAY_MQTT_USERNAME=<dedicated EMQX relay username>
RELAY_MQTT_PASSWORD=<dedicated EMQX relay password>
```

Optional defaults:

```text
RELAY_MQTT_BROKER_HOST=wfde4d21.ala.asia-southeast1.emqxsl.com
RELAY_MQTT_BROKER_PORT=8883
RELAY_MQTT_CLIENT_ID=civictwin_adyar_relay_01
```

The relay uses Python's default trusted CA certificates and hostname
verification through `ssl.create_default_context()`. It never uses insecure
TLS. The EMQX credentials are separate from the existing backend credentials.

The dedicated EMQX account needs only publish permission for:

```text
civictwin/v1/zones/zone_01/telemetry
```

It does not need subscribe, status-topic, or alert-topic permissions.

## Run locally

From the repository root, install the relay dependencies and run:

```bash
python3 -m pip install -r relay/requirements.txt
uvicorn relay.app:app --host 0.0.0.0 --port 8000
```

Do not put real values in tracked files. On Render, configure the three
required environment variables in the service settings and set the start
command to:

```text
uvicorn relay.app:app --host 0.0.0.0 --port $PORT
```

Render provides the public HTTPS URL. Wokwi should POST JSON to:

```text
https://<render-host>/api/wokwi/adyar/telemetry
```

with:

```text
Authorization: Bearer <WOKWI_ADYAR_INGEST_TOKEN>
Content-Type: application/json
```

The token embedded in a public Wokwi sketch can be copied. Use a
zone-specific token, keep its permissions limited to this endpoint, rotate it
if exposed, and never reuse an EMQX password as the HTTP token.
