#include <Arduino.h>
#include <ctype.h>
#include <string.h>

const uint8_t PWM_RED_PIN = 3;
const uint8_t PWM_GREEN_PIN = 5;
const uint8_t PWM_BLUE_PIN = 6;

struct ColorConfig {
  const char *name;
  uint8_t pins[3];
  uint8_t pin_count;
};

const ColorConfig COLOR_TABLE[] = {
    {"red", {PWM_RED_PIN, 0, 0}, 1},
    {"green", {PWM_GREEN_PIN, 0, 0}, 1},
    {"blue", {PWM_BLUE_PIN, 0, 0}, 1},
    {"white", {PWM_RED_PIN, PWM_GREEN_PIN, PWM_BLUE_PIN}, 3},
};

const size_t COLOR_TABLE_SIZE = sizeof(COLOR_TABLE) / sizeof(COLOR_TABLE[0]);

char *trimWhitespace(char *value) {
  if (value == nullptr) {
    return nullptr;
  }

  while (*value && isspace(*value)) {
    value++;
  }

  size_t length = strlen(value);
  while (length > 0 && isspace(value[length - 1])) {
    value[length - 1] = '\0';
    length--;
  }
  return value;
}

void toLowercase(char *value) {
  if (value == nullptr) {
    return;
  }

  for (char *p = value; *p; ++p) {
    *p = static_cast<char>(tolower(*p));
  }
}

bool tryParseInt(const char *token, int *out_value) {
  if (token == nullptr || *token == '\0') {
    return false;
  }

  bool is_negative = false;
  if (*token == '+' || *token == '-') {
    is_negative = (*token == '-');
    token++;
  }

  if (*token == '\0') {
    return false;
  }

  long value = 0;
  for (const char *p = token; *p; ++p) {
    if (!isdigit(*p)) {
      return false;
    }
    value = value * 10 + (*p - '0');
    if (value > 32767) {  // guard against overflow
      return false;
    }
  }

  if (is_negative) {
    value = -value;
  }

  *out_value = static_cast<int>(value);
  return true;
}

const ColorConfig *resolveColor(char *token) {
  if (token == nullptr) {
    return nullptr;
  }

  char *normalized = trimWhitespace(token);
  toLowercase(normalized);

  for (size_t i = 0; i < COLOR_TABLE_SIZE; ++i) {
    if (strcmp(normalized, COLOR_TABLE[i].name) == 0) {
      return &COLOR_TABLE[i];
    }
  }
  return nullptr;
}

void setChannelsIntensity(const ColorConfig &color, int intensity) {
  for (uint8_t i = 0; i < color.pin_count; ++i) {
    analogWrite(color.pins[i], intensity);
  }
}

void disableChannels(const ColorConfig &color) {
  setChannelsIntensity(color, 0);
}

void ensureAllPinsLow() {
  analogWrite(PWM_RED_PIN, 0);
  analogWrite(PWM_GREEN_PIN, 0);
  analogWrite(PWM_BLUE_PIN, 0);
}

void processCommand(int duration, int intensity, int frequency,
                    const ColorConfig &color) {
  unsigned long functionStartTime = micros();

  if (frequency == 0) {
    unsigned long pwmStartTime = micros();
    setChannelsIntensity(color, intensity);
    unsigned long pwmEndTime = micros();

    Serial.print("PWM setup time (us): ");
    Serial.println(pwmEndTime - pwmStartTime);

    delay(duration);
    disableChannels(color);
  } else {
    unsigned long startTime = millis();
    unsigned long endTime = startTime + duration;

    Serial.print("Square wave generation start (us): ");
    Serial.println(micros() - functionStartTime);

    if (frequency > 500) {
      unsigned long periodMicros = 1000000UL / frequency;
      unsigned long halfPeriodMicros = periodMicros / 2;

      Serial.print("Period calculation time (us): ");
      Serial.println(micros() - functionStartTime);
      Serial.print("Half period (us): ");
      Serial.println(halfPeriodMicros);

      unsigned long cycleCount = 0;
      unsigned long cycleStartTime = micros();

      while (millis() < endTime) {
        setChannelsIntensity(color, intensity);
        delayMicroseconds(halfPeriodMicros);
        disableChannels(color);
        delayMicroseconds(halfPeriodMicros);
        cycleCount++;
      }

      unsigned long cycleEndTime = micros();
      Serial.print("Total cycles: ");
      Serial.println(cycleCount);
      Serial.print("Average cycle time (us): ");
      Serial.println((cycleEndTime - cycleStartTime) /
                     (cycleCount > 0 ? cycleCount : 1));
    } else {
      unsigned long periodMs = 1000UL / frequency;
      unsigned long halfPeriodMs = periodMs / 2;

      Serial.print("Period calculation time (us): ");
      Serial.println(micros() - functionStartTime);
      Serial.print("Half period (ms): ");
      Serial.println(halfPeriodMs);

      unsigned long cycleCount = 0;
      unsigned long cycleStartTime = micros();

      while (millis() < endTime) {
        setChannelsIntensity(color, intensity);
        delay(halfPeriodMs);
        disableChannels(color);
        delay(halfPeriodMs);
        cycleCount++;
      }

      unsigned long cycleEndTime = micros();
      Serial.print("Total cycles: ");
      Serial.println(cycleCount);
      Serial.print("Average cycle time (us): ");
      Serial.println((cycleEndTime - cycleStartTime) /
                     (cycleCount > 0 ? cycleCount : 1));
    }
  }
}

void setup() {
  pinMode(PWM_RED_PIN, OUTPUT);
  pinMode(PWM_GREEN_PIN, OUTPUT);
  pinMode(PWM_BLUE_PIN, OUTPUT);
  ensureAllPinsLow();

  Serial.begin(115200);
  Serial.println(
      "Arduino ready. Send commands in format: <duration,intensity,frequency,color>");
}

void loop() {
  if (Serial.available() > 0) {
    unsigned long commandStartTime = micros();

    char c = Serial.read();
    if (c == '<') {
      unsigned long parseStartTime = micros();
      char buffer[48];
      size_t length = Serial.readBytesUntil('>', buffer, sizeof(buffer) - 1);
      buffer[length] = '\0';

      bool parseError = false;
      int duration = 0;
      int intensity = 0;
      int frequency = 0;
      const ColorConfig *colorConfig = nullptr;

      char *token = strtok(buffer, ",");
      if (!tryParseInt(trimWhitespace(token), &duration)) {
        parseError = true;
        Serial.println("Error: invalid duration token.");
      }

      if (!parseError) {
        token = strtok(nullptr, ",");
        if (!tryParseInt(trimWhitespace(token), &intensity)) {
          parseError = true;
          Serial.println("Error: invalid intensity token.");
        }
      }

      if (!parseError) {
        token = strtok(nullptr, ",");
        if (!tryParseInt(trimWhitespace(token), &frequency)) {
          parseError = true;
          Serial.println("Error: invalid frequency token.");
        }
      }

      if (!parseError) {
        token = strtok(nullptr, ",");
        if (token == nullptr) {
          parseError = true;
          Serial.println("Error: missing color token.");
        } else {
          colorConfig = resolveColor(token);
          if (colorConfig == nullptr) {
            parseError = true;
            Serial.println(
                "Error: invalid color. Use one of: red, green, blue, white.");
          }
        }
      }

      while (Serial.available() > 0 &&
             (Serial.peek() == '\n' || Serial.peek() == '\r')) {
        Serial.read();
      }

      if (!parseError) {
        duration = constrain(duration, 0, 3000);
        intensity = constrain(intensity, 0, 255);
        if (frequency < 0) {
          frequency = 0;
        }

        unsigned long parseEndTime = micros();
        unsigned long executeStartTime = micros();
        processCommand(duration, intensity, frequency, *colorConfig);
        unsigned long executeEndTime = micros();

        Serial.print("Parse time (us): ");
        Serial.println(parseEndTime - parseStartTime);
        Serial.print("Execution time (us): ");
        Serial.println(executeEndTime - executeStartTime);
        Serial.print("Total processing time (us): ");
        Serial.println(executeEndTime - commandStartTime);
        Serial.print("Command processed: <");
        Serial.print(duration);
        Serial.print(",");
        Serial.print(intensity);
        Serial.print(",");
        Serial.print(frequency);
        Serial.print(",");
        Serial.print(colorConfig->name);
        Serial.println(">");
      } else {
        // Flush remnants if we hit a parse error
        while (Serial.available() > 0) {
          Serial.read();
        }
      }
    }
  }
}