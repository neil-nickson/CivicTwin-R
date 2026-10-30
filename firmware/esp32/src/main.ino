/*
 * CivicTwin-R — ESP32 Firmware
 * Water-Level Monitoring with HC-SR04 Ultrasonic Sensor
 *
 * Reads distance from an HC-SR04 sensor, calculates water depth,
 * evaluates local thresholds with hysteresis, and publishes telemetry over MQTT/TLS.
 *
 * Pin Connections:
 *   HC-SR04 TRIG -> GPIO 5
 *   HC-SR04 ECHO -> GPIO 18 (use 2.2k/3.3k resistor divider on physical HW for 3.3V)
 *   HC-SR04 VCC  -> 5V (VIN)
 *   HC-SR04 GND  -> GND
 *   Status LED   -> GPIO 2
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

// ── Project Configuration ──────────────────────────────────────────
// Copy this firmware into each Wokwi project and change only the
// project-specific identity values below. Keep the topic zone segment
// derived from ZONE_ID so topic and payload identities remain aligned.
//
// Canonical default: Adyar / zone_01.

// WiFi Configuration
const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";

// EMQX Cloud MQTT/TLS configuration.
const char* MQTT_BROKER = "wfde4d21.ala.asia-southeast1.emqxsl.com";
const int   MQTT_PORT = 8883;
const char* MQTT_USER = "civictwin_device";
// Replace locally with the dedicated EMQX device password. Never commit it.
const char* MQTT_PASS = "REPLACE_WITH_EMQX_DEVICE_PASSWORD";
const char* MQTT_CLIENT_ID = "civictwin_adyar_01";
// DigiCert Global Root G2, which signs the EMQX Cloud endpoint certificate.
// Do not replace this with setInsecure() or disable certificate validation.
const char* EMQX_ROOT_CA_CERTIFICATE = R"_CERT(
-----BEGIN CERTIFICATE-----
MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI
2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx
1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ
q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz
tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ
vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV
5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY
1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4
NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG
Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91
8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe
pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl
MrY=
-----END CERTIFICATE-----
)_CERT";

// Per-project identity
const char* DEVICE_ID      = "node_adyar_01";
#define ZONE_ID "zone_01"

// MQTT Topics — derived from the configured canonical zone ID.
const char* TOPIC_TELEMETRY = "civictwin/v1/zones/" ZONE_ID "/telemetry";
const char* TOPIC_STATUS    = "civictwin/v1/zones/" ZONE_ID "/status";
const char* TOPIC_ALERTS    = "civictwin/v1/zones/" ZONE_ID "/alerts";

#ifndef DATA_SOURCE
#define DATA_SOURCE "WOKWI_SIMULATED"
#endif

// Sensor Geometry and Pins
const int   TRIG_PIN            = 5;
const int   ECHO_PIN            = 18;
const int   LED_PIN             = 2;
// Wokwi potentiometer stand-in for an analog rain sensor.
// This is an uncalibrated indicator, not rainfall in millimetres.
const int   RAIN_SENSOR_PIN      = 36;  // ESP32 VP / ADC1 input
const int   RAIN_ADC_MIN         = 0;
const int   RAIN_ADC_MAX         = 4095;
// Prototype container reference height (cm).
// Formula assumes a fixed sensor mount above the water surface:
//   Water depth (cm) = container reference height (cm) - measured distance (cm)
//   Water level (%)  = 100 * water depth / container reference height
const float CONTAINER_HEIGHT_CM = 30.0;
const float SENSOR_MIN_CM       = 2.0;
const float SENSOR_MAX_CM       = 400.0;
const float SPEED_OF_SOUND      = 0.0343;

// Intervals
const unsigned long READ_INTERVAL_MS   = 5000;
const unsigned long RECONNECT_DELAY_MS = 5000;
const unsigned long WIFI_TIMEOUT_MS    = 10000;
const unsigned long STATUS_INTERVAL_MS = 30000;

// Thresholds and Hysteresis
// Disclaimer: These are configurable prototype thresholds for laboratory simulation
// and are NOT officially validated flood warning thresholds for Chennai river basins.
const float WARN_THRESHOLD_PCT = 70.0;
const float CRIT_THRESHOLD_PCT = 85.0;
const float HYSTERESIS_PCT     = 3.0;
const int   FILTER_READINGS    = 5;

unsigned long lastReadTime    = 0;
unsigned long lastStatusTime  = 0;
unsigned long sequence        = 0;
bool          mqttConnected   = false;
String        currentAlert    = "normal";
float         lastValidLevel  = -1.0;
int           failedReadings  = 0;
const int     MAX_FAILED      = 5;

WiFiClientSecure mqttTlsClient;
PubSubClient mqttClient(mqttTlsClient);

int readRainSensorRaw() {
    int raw = analogRead(RAIN_SENSOR_PIN);
    return constrain(raw, RAIN_ADC_MIN, RAIN_ADC_MAX);
}

void connectMQTT() {
    if (mqttClient.connected()) {
        mqttConnected = true;
        return;
    }

    Serial.printf("[MQTT] Connecting to %s:%d...\n", MQTT_BROKER, MQTT_PORT);
    bool connected = mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS);

    if (connected) {
        mqttConnected = true;
        Serial.println("[MQTT] Connected");
    } else {
        mqttConnected = false;
        Serial.printf("[MQTT] Connect failed (rc=%d)\n", mqttClient.state());
    }
}

float rainIndicatorPct(int raw) {
    return (static_cast<float>(raw - RAIN_ADC_MIN) /
            static_cast<float>(RAIN_ADC_MAX - RAIN_ADC_MIN)) * 100.0;
}

float readDistanceCm() {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);

    long duration = pulseIn(ECHO_PIN, HIGH, 25000);
    if (duration == 0) {
        return -1.0;
    }
    return (duration * SPEED_OF_SOUND) / 2.0;
}

float getMedianDistance() {
    float readings[FILTER_READINGS];
    int validCount = 0;

    for (int i = 0; i < FILTER_READINGS; i++) {
        float d = readDistanceCm();
        if (d > 0) {
            readings[validCount++] = d;
        }
        delay(30);
    }

    if (validCount == 0) {
        return -1.0;
    }

    for (int i = 1; i < validCount; i++) {
        float key = readings[i];
        int j = i - 1;
        while (j >= 0 && readings[j] > key) {
            readings[j + 1] = readings[j];
            j--;
        }
        readings[j + 1] = key;
    }

    return readings[validCount / 2];
}

void connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;

    Serial.printf("[WiFi] Connecting to %s...\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
        delay(250);
        Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WiFi] Connected: %s\n", WiFi.localIP().toString().c_str());
        configTime(0, 0, "pool.ntp.org");
    } else {
        Serial.println("\n[WiFi] Connection timeout; will retry");
    }
}

String getISO8601Timestamp() {
    time_t now;
    time(&now);
    struct tm timeinfo;
    gmtime_r(&now, &timeinfo);
    char buf[30];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return String(buf);
}

void publishTelemetry(float distance, float waterLevel, float waterPct, const char* sensorStatus) {
    sequence++;
    int rainRaw = readRainSensorRaw();
    float rainPct = rainIndicatorPct(rainRaw);

    JsonDocument doc;
    doc["schema_version"] = "1.0";
    doc["device_id"]      = DEVICE_ID;
    doc["zone_id"]        = ZONE_ID;
    doc["sequence"]       = sequence;
    doc["timestamp"]      = getISO8601Timestamp();

    if (strcmp(sensorStatus, "valid") == 0) {
        doc["distance_cm"]    = round(distance * 100) / 100.0;
        doc["water_level_cm"] = round(waterLevel * 100) / 100.0;
        doc["water_level_pct"]= round(waterPct * 100) / 100.0;
    } else {
        // Do not fabricate zero readings for sensor failures or timeouts
        doc["distance_cm"]    = nullptr;
        doc["water_level_cm"] = nullptr;
        doc["water_level_pct"]= nullptr;
    }
    doc["sensor_status"]  = sensorStatus;
    doc["source"]         = DATA_SOURCE;
    doc["rain_sensor_raw"] = rainRaw;
    doc["rain_indicator_pct"] = round(rainPct * 100) / 100.0;
    JsonArray qualityFlags = doc["quality_flags"].to<JsonArray>();
    qualityFlags.add("rain_indicator_uncalibrated");

    char payload[512];
    serializeJson(doc, payload, sizeof(payload));

    if (mqttConnected && mqttClient.connected()) {
        bool sent = mqttClient.publish(TOPIC_TELEMETRY, payload);
        if (sent) {
            Serial.printf("[TX] seq=%lu level=%.1fcm (%.1f%%) rain=%.1f%% status=%s\n",
                          sequence, waterLevel, waterPct, rainPct, sensorStatus);
        } else {
            Serial.println("[TX] Publish failed");
        }
    } else {
        Serial.printf("[OFFLINE] seq=%lu level=%.1fcm (%.1f%%) rain=%.1f%% status=%s\n",
                      sequence, waterLevel, waterPct, rainPct, sensorStatus);
    }
}

void publishAlert(const char* level, float waterPct) {
    JsonDocument doc;
    doc["zone_id"]     = ZONE_ID;
    doc["device_id"]   = DEVICE_ID;
    doc["alert_level"] = level;
    doc["water_pct"]   = waterPct;
    doc["threshold"]   = (strcmp(level, "critical") == 0) ? CRIT_THRESHOLD_PCT : WARN_THRESHOLD_PCT;
    doc["timestamp"]   = getISO8601Timestamp();

    char payload[256];
    serializeJson(doc, payload, sizeof(payload));

    if (mqttConnected && mqttClient.connected()) {
        if (!mqttClient.publish(TOPIC_ALERTS, payload)) {
            Serial.println("[TX] Alert publish failed");
        }
    }
    Serial.printf("[ALERT] %s: water level at %.1f%%\n", level, waterPct);
}

void evaluateThreshold(float waterPct) {
    String newAlert = "normal";

    if (waterPct >= CRIT_THRESHOLD_PCT) {
        newAlert = "critical";
    } else if (waterPct >= WARN_THRESHOLD_PCT) {
        newAlert = "warning";
    } else if (currentAlert == "warning" && waterPct >= (WARN_THRESHOLD_PCT - HYSTERESIS_PCT)) {
        newAlert = "warning";
    } else if (currentAlert == "critical" && waterPct >= (CRIT_THRESHOLD_PCT - HYSTERESIS_PCT)) {
        newAlert = "critical";
    }

    if (newAlert != currentAlert) {
        currentAlert = newAlert;
        if (currentAlert == "critical" || currentAlert == "warning") {
            publishAlert(currentAlert.c_str(), waterPct);
            digitalWrite(LED_PIN, HIGH);
        } else {
            publishAlert("normal", waterPct);
            digitalWrite(LED_PIN, LOW);
        }
    }
}

void setup() {
    Serial.begin(115200);
    Serial.println("CivicTwin-R Node 01 - Water Level Monitor");

    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    connectWiFi();

    mqttTlsClient.setCACert(EMQX_ROOT_CA_CERTIFICATE);
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setBufferSize(512);
    connectMQTT();
}

void loop() {
    unsigned long now = millis();

    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
    }
    if (!mqttClient.connected()) {
        mqttConnected = false;
        if (now - lastReadTime > RECONNECT_DELAY_MS) {
            connectMQTT();
        }
    }
    mqttClient.loop();

    if (now - lastReadTime >= READ_INTERVAL_MS) {
        lastReadTime = now;

        float distance = getMedianDistance();

        // Safe handling of invalid echo readings, timeouts, and out-of-range bounds
        if (distance < 0 || distance < SENSOR_MIN_CM || distance > SENSOR_MAX_CM) {
            failedReadings++;

            if (failedReadings >= MAX_FAILED) {
                // Reject invalid measurement: do NOT publish dummy 0.0 water level
                publishTelemetry(-1.0, -1.0, -1.0, "invalid");
                Serial.printf("[SENSOR] %d consecutive failures (out of range/timeout)\n", failedReadings);
            } else if (lastValidLevel >= 0) {
                float pct = (lastValidLevel / CONTAINER_HEIGHT_CM) * 100.0;
                evaluateThreshold(pct);
            }
        } else {
            failedReadings = 0;

            // Water depth (cm) = container reference height (cm) - measured distance (cm)
            float waterLevel = CONTAINER_HEIGHT_CM - distance;
            if (waterLevel < 0.0) waterLevel = 0.0;
            if (waterLevel > CONTAINER_HEIGHT_CM) waterLevel = CONTAINER_HEIGHT_CM;

            // Water level (%) = 100 * water depth / container reference height
            float waterPct = (waterLevel / CONTAINER_HEIGHT_CM) * 100.0;
            if (waterPct < 0.0) waterPct = 0.0;
            if (waterPct > 100.0) waterPct = 100.0;
            lastValidLevel = waterLevel;

            publishTelemetry(distance, waterLevel, waterPct, "valid");
            evaluateThreshold(waterPct);
        }
    }

    if (now - lastStatusTime >= STATUS_INTERVAL_MS) {
        lastStatusTime = now;
        JsonDocument doc;
        doc["device_id"]   = DEVICE_ID;
        doc["wifi"]        = WiFi.status() == WL_CONNECTED;
        doc["mqtt"]        = mqttConnected;
        doc["uptime_sec"]  = millis() / 1000;
        doc["sequence"]    = sequence;
        doc["alert_state"] = currentAlert;
        doc["heap_free"]   = ESP.getFreeHeap();

        char payload[256];
        serializeJson(doc, payload, sizeof(payload));

        if (mqttConnected && mqttClient.connected()) {
            if (!mqttClient.publish(TOPIC_STATUS, payload)) {
                Serial.println("[TX] Status publish failed");
            }
        }
        Serial.printf("[STATUS] uptime=%lus seq=%lu alert=%s heap=%u\n",
                      millis() / 1000, sequence, currentAlert.c_str(), ESP.getFreeHeap());
    }
}
