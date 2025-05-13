const int pwmPin = 9;  // PWM output pin

void setup() {
  pinMode(pwmPin, OUTPUT);
  Serial.begin(115200);  // High baud rate for minimal latency
  Serial.println("Arduino ready. Send commands in format: <duration,intensity,frequency>");
}

void loop() {
  if (Serial.available() > 0) {
    // Record start time when first character is available
    unsigned long commandStartTime = micros();
    unsigned long parseStartTime = 0;
    unsigned long parseEndTime = 0;
    unsigned long executeStartTime = 0;
    unsigned long executeEndTime = 0;
    
    // Check for the start of a command
    char c = Serial.read();
    if (c == '<') {
      parseStartTime = micros();
      
      // Read the parameters
      int duration = Serial.parseInt();
      if (Serial.read() == ',') {
        int intensity = Serial.parseInt();
        if (Serial.read() == ',') {
          int frequency = Serial.parseInt();
          if (Serial.read() == '>') {
            // Validate parameters
            duration = constrain(duration, 0, 3000);
            intensity = constrain(intensity, 0, 255);
            
            parseEndTime = micros();
            
            // Process the command
            executeStartTime = micros();
            processCommand(duration, intensity, frequency);
            executeEndTime = micros();
            
            // Print timing information
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
            Serial.println(">");
          }
        }
      }
      
      // Flush any remaining characters
      while (Serial.available() > 0) {
        Serial.read();
      }
    }
  }
}

void processCommand(int duration, int intensity, int frequency) {
  unsigned long functionStartTime = micros();
  
  if (frequency == 0) {
    // Continuous PWM at specified intensity
    unsigned long pwmStartTime = micros();
    analogWrite(pwmPin, intensity);
    unsigned long pwmEndTime = micros();
    
    Serial.print("PWM setup time (us): ");
    Serial.println(pwmEndTime - pwmStartTime);
    
    delay(duration);
    analogWrite(pwmPin, 0);
  } else {
    // Generate a square wave with the specified frequency
    unsigned long startTime = millis();
    unsigned long endTime = startTime + duration;
    
    Serial.print("Square wave generation start (us): ");
    Serial.println(micros() - functionStartTime);
    
    // Calculate the period based on frequency
    if (frequency > 500) {
      // For high frequencies, use microseconds for more precise timing
      unsigned long periodMicros = 1000000 / frequency;
      unsigned long halfPeriodMicros = periodMicros / 2;
      
      // Print period calculation time
      Serial.print("Period calculation time (us): ");
      Serial.println(micros() - functionStartTime);
      Serial.print("Half period (us): ");
      Serial.println(halfPeriodMicros);
      
      unsigned long cycleCount = 0;
      unsigned long cycleStartTime = micros();
      
      while (millis() < endTime) {
        analogWrite(pwmPin, intensity);
        delayMicroseconds(halfPeriodMicros);
        analogWrite(pwmPin, 0);
        delayMicroseconds(halfPeriodMicros);
        cycleCount++;
      }
      
      unsigned long cycleEndTime = micros();
      Serial.print("Total cycles: ");
      Serial.println(cycleCount);
      Serial.print("Average cycle time (us): ");
      Serial.println((cycleEndTime - cycleStartTime) / (cycleCount > 0 ? cycleCount : 1));
    } else {
      // For lower frequencies, use milliseconds
      unsigned long periodMs = 1000 / frequency;
      unsigned long halfPeriodMs = periodMs / 2;
      
      // Print period calculation time
      Serial.print("Period calculation time (us): ");
      Serial.println(micros() - functionStartTime);
      Serial.print("Half period (ms): ");
      Serial.println(halfPeriodMs);
      
      unsigned long cycleCount = 0;
      unsigned long cycleStartTime = micros();
      
      while (millis() < endTime) {
        analogWrite(pwmPin, intensity);
        delay(halfPeriodMs);
        analogWrite(pwmPin, 0);
        delay(halfPeriodMs);
        cycleCount++;
      }
      
      unsigned long cycleEndTime = micros();
      Serial.print("Total cycles: ");
      Serial.println(cycleCount);
      Serial.print("Average cycle time (us): ");
      Serial.println((cycleEndTime - cycleStartTime) / (cycleCount > 0 ? cycleCount : 1));
    }
  }
}