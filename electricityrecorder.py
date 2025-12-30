import hassapi as hass
import psycopg2
import datetime
from datetime import timedelta

class ElectricityRecorder(hass.Hass):

    def initialize(self):
        # Database Credentials
        self.db_host = self.args.get("db_host", "localhost")
        self.db_name = self.args.get("db_name", "homeassistant")
        self.db_user = self.args.get("db_user", "postgres")
        self.db_pass = self.args.get("db_pass", "password")
        
        # Global Default Price
        self.default_price_entity = self.args.get("price_entity")

        # Configuration for Meters
        self.meters = {}
        raw_meters = self.args.get("meters", [])

        if not raw_meters:
            self.log("No meters configured! Please add a 'meters' list to apps.yaml.", level="WARNING")

        for meter_config in raw_meters:
            name = meter_config.get("name")
            entity = meter_config.get("entity")
            is_cost = meter_config.get("is_cost", True)
            specific_price_entity = meter_config.get("price_entity")

            if name and entity:
                self.meters[name] = {
                    "entity": entity,
                    "is_cost": is_cost,
                    "price_entity": specific_price_entity
                }
                price_source = specific_price_entity if specific_price_entity else "Default Global"
                self.log(f"Registered meter: {name} - Price Source: {price_source}")
            else:
                self.log(f"Skipping invalid meter config: {meter_config}", level="WARNING")
        
        # Storage for previous readings
        self.last_readings = {}
        
        # Try to restore last readings from DB to survive restarts
        self.restore_last_readings()

        # Schedule Logic: Run every 15 minutes aligned to the clock
        now = datetime.datetime.now()
        minutes_to_next_quarter = 15 - (now.minute % 15)
        start_time = now + timedelta(minutes=minutes_to_next_quarter)
        start_time = start_time.replace(second=0, microsecond=0)

        self.log(f"Recorder initialized. Active meters: {list(self.meters.keys())}")
        self.log(f"First run scheduled for: {start_time}")
        
        self.run_every(self.record_usage, start_time, 900)

    def restore_last_readings(self):
        connection = None
        try:
            connection = psycopg2.connect(
                host=self.db_host, database=self.db_name,
                user=self.db_user, password=self.db_pass
            )
            cursor = connection.cursor()
            self.log("Attempting to restore previous meter readings from database...")

            for meter_type in self.meters:
                query = """
                    SELECT meter_reading 
                    FROM energy_measurements 
                    WHERE measurement_type = %s 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """
                cursor.execute(query, (meter_type,))
                result = cursor.fetchone()
                if result:
                    self.last_readings[meter_type] = result[0]
                    self.log(f"Restored {meter_type}: {result[0]}")
                else:
                    self.log(f"No history for {meter_type}, starting fresh.")
        except (Exception, psycopg2.Error) as error:
            self.log(f"Error restoring state from DB: {error}", level="WARNING")
        finally:
            if connection:
                cursor.close()
                connection.close()

    def get_float_state(self, entity_id):
        if not entity_id: return None
        try:
            state = self.get_state(entity_id)
            if state in ["unknown", "unavailable", None]: return None
            return float(state)
        except ValueError: return None

    def record_usage(self, kwargs):
        now = datetime.datetime.now()
        minute_aligned = (now.minute // 15) * 15
        record_timestamp = now.replace(minute=minute_aligned, second=0, microsecond=0)
        
        # 1. Fetch Global Price
        global_price_state = self.get_float_state(self.default_price_entity)
        default_price = global_price_state if global_price_state is not None else 0.0
        default_price = round(default_price, 4)

        # Temp storage for this interval's data before writing
        # Structure: { "meter_name": { "reading": 100, "delta": 5, "price": 0.2, "cost": 1.0, "is_cost": T/F } }
        batch_data = {}

        # 2. PHASE 1: Calculate Deltas and Base Prices
        for meter_type, config in self.meters.items():
            entity_id = config["entity"]
            raw_reading = self.get_float_state(entity_id)

            if raw_reading is None:
                self.log(f"Meter {meter_type} is unavailable. Skipping.", level="WARNING")
                continue
            
            current_reading = round(raw_reading, 4)

            # Determine Base Price
            if config["price_entity"]:
                spec_price = self.get_float_state(config["price_entity"])
                current_price = spec_price if spec_price is not None else default_price
            else:
                current_price = default_price
            current_price = round(current_price, 4)

            # Baseline check
            if meter_type not in self.last_readings:
                self.last_readings[meter_type] = current_reading
                continue

            previous_reading = self.last_readings[meter_type]
            
            # Robustness: Jump from Zero
            if previous_reading == 0 and current_reading > 10:
                self.log(f"Meter {meter_type} jump from 0 to {current_reading}. Resetting baseline.", level="WARNING")
                self.last_readings[meter_type] = current_reading
                continue

            # Calculate Delta
            if current_reading < previous_reading:
                delta_kwh = current_reading # Reset detected
                self.log(f"Meter {meter_type} reset. Delta: {delta_kwh}")
            else:
                delta_kwh = current_reading - previous_reading

            delta_kwh = round(delta_kwh, 4)

            # Calculate Initial Financials
            cost_amount = delta_kwh * current_price
            if not config["is_cost"]:
                cost_amount = -cost_amount
            
            # Store in batch
            batch_data[meter_type] = {
                "reading": current_reading,
                "delta": delta_kwh,
                "price": current_price,
                "cost": round(cost_amount, 4),
                "is_cost": config["is_cost"]
            }
            
            # Update memory immediately
            self.last_readings[meter_type] = current_reading

        # 3. PHASE 2: Apply Smart Logic (Solar/Battery Export Split)
        self.apply_smart_logic(batch_data, default_price)

        # 4. PHASE 3: Write to Database
        if batch_data:
            self.write_to_db(batch_data, record_timestamp)

    def apply_smart_logic(self, batch_data, import_price):
        """
        Adjusts solar and battery prices based on grid export.
        Logic: 
        1. Solar covers Export first. (Valued at Export Price)
        2. Remaining Solar is Self-Consumed. (Valued at Import Price)
        3. Remaining Export is covered by Battery.
        """
        # Ensure we have the necessary meters
        if "grid_export" not in batch_data:
            return

        grid_export_delta = batch_data["grid_export"]["delta"]
        grid_export_price = batch_data["grid_export"]["price"] # This is the export tariff
        
        # We need to track how much export capacity is "used up" by solar
        remaining_export_capacity = grid_export_delta

        # --- SOLAR LOGIC ---
        if "solar_production" in batch_data and batch_data["solar_production"]["delta"] > 0:
            solar_total = batch_data["solar_production"]["delta"]
            
            # How much solar was exported? (Up to the amount of grid export)
            solar_exported = min(solar_total, remaining_export_capacity)
            solar_self_consumed = solar_total - solar_exported
            
            # Weighted Value:
            # Self Consumed = Savings = Import Price (Avoided Cost)
            # Exported = Earnings = Export Price
            value_saved = solar_self_consumed * import_price
            value_earned = solar_exported * grid_export_price
            
            total_value = value_saved + value_earned
            weighted_price = total_value / solar_total
            
            # Update Batch Data
            # Solar is_cost=False, so cost needs to be negative (Profit/Savings)
            batch_data["solar_production"]["price"] = round(weighted_price, 4)
            batch_data["solar_production"]["cost"] = round(-total_value, 4)
            
            # Reduce remaining export capacity for battery
            remaining_export_capacity = max(0, remaining_export_capacity - solar_exported)

        # --- BATTERY DISCHARGE LOGIC ---
        if "battery_discharge" in batch_data and batch_data["battery_discharge"]["delta"] > 0:
            batt_total = batch_data["battery_discharge"]["delta"]
            
            # How much battery was exported? (Whatever solar didn't cover)
            batt_exported = min(batt_total, remaining_export_capacity)
            batt_self_consumed = batt_total - batt_exported
            
            value_saved = batt_self_consumed * import_price
            value_earned = batt_exported * grid_export_price
            
            total_value = value_saved + value_earned
            weighted_price = total_value / batt_total
            
            # Update Batch Data
            batch_data["battery_discharge"]["price"] = round(weighted_price, 4)
            batch_data["battery_discharge"]["cost"] = round(-total_value, 4)

    def write_to_db(self, batch_data, timestamp):
        connection = None
        try:
            connection = psycopg2.connect(
                host=self.db_host, database=self.db_name,
                user=self.db_user, password=self.db_pass
            )
            cursor = connection.cursor()

            query = """
                INSERT INTO energy_measurements 
                (timestamp, measurement_type, meter_reading, usage_kwh, price_per_kwh, cost_amount)
                VALUES (%s, %s, %s, %s, %s, %s)
            """

            for meter_type, data in batch_data.items():
                cursor.execute(query, (
                    timestamp, 
                    meter_type, 
                    data["reading"], 
                    data["delta"], 
                    data["price"], 
                    data["cost"]
                ))
            
            connection.commit()
            self.log(f"Recorded smart data for {timestamp}. Meters: {len(batch_data)}")

        except (Exception, psycopg2.Error) as error:
            self.log(f"Database error: {error}", level="ERROR")
        finally:
            if connection:
                cursor.close()
                connection.close()
