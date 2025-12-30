# Electricity Recorder (AppDaemon)
**Electricity Recorder** is a sophisticated AppDaemon application designed for Home Assistant users who require precise, granular control over their energy data. By leveraging a PostgreSQL database, this tool records electricity consumption, production, and costs every 15 minutes, providing a solid foundation for advanced energy dashboards (e.g., Grafana) and detailed financial ROI analysis.

Unlike standard energy integrations, this recorder applies logic to determine the true value of your solar and battery energy (Avoided Cost vs. Export Revenue) based on real-time grid interaction.

## Key Features
- **Precision Interval Recording:** Captures data exactly on the quarter-hour (00, 15, 30, 45), ensuring alignment with standard utility billing periods and preventing data drift.
- **Smart Financial Logic:**
  - **Avoided Cost Calculation:** Automatically values self-consumed solar/battery energy at the import rate (savings).
  - **Export Revenue:** Values energy sent to the grid at your specific export tariff.
  - **Weighted Averages:** When a 15-minute block contains both self-consumption and export, the system calculates a precise weighted average price per kWh.
- **Resilient Architecture:**
  - **Robust error handling** for sensor unavailability (prevents zero-spikes).
  - **Automatic detection** of daily meter resets to ensure continuity of cumulative counters.
  - **State restoration** after restart to prevent data gaps.
- **Flexible Configuration:** Supports any number of meters via simple YAML configuration, allowing for specific price overrides per meter (e.g., distinct Import and Export tariffs).

## Installation & Setup
### Prerequisites
- AppDaemon 4.x running on Home Assistant.
- PostgreSQL Database access.
- Python Dependencies: You must add psycopg2 (or psycopg2-binary) to your AppDaemon configuration packages.
- Energy consumption sensors in Home Assistant for
  - `grid_import`, `grid_export`, `solar_production`, `battery_charge` and `battery_discharge`
  - `misc_consumption`; this can be a calculated sensor for the consumption of the whole house. Equal to `(grid_import - grid_export) + solar_production + (battery_discharge - battery_charge)`
  - Optionally you can add other (metered) consumers; make sure to not include them in `misc_consumption`

**1. Database Initialization**
Before running the application, you must initialize your PostgreSQL database schema. The system uses a normalized table structure to store all measurement types (grid, solar, battery) in a single, efficient table.
```
-- Drop the old table if it exists (WARNING: This deletes existing data)
-- DROP TABLE IF EXISTS electricity_records;

CREATE TABLE energy_measurements (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- Identifies what this row is measuring (e.g., 'grid_import', 'grid_export', 'solar', 'battery')
    measurement_type VARCHAR(50) NOT NULL,
    
    -- The raw counter value from the meter (cumulative)
    meter_reading DOUBLE PRECISION,
    
    -- The amount used/generated in this 15-minute window
    usage_kwh DOUBLE PRECISION,
    
    -- Financial data
    price_per_kwh DOUBLE PRECISION,
    cost_amount DOUBLE PRECISION  -- Positive (+) for Cost, Negative (-) for Profit
);

-- Index for fast time-based queries
CREATE INDEX idx_energy_timestamp ON energy_measurements(timestamp);
-- Index for filtering by type (e.g., "Show me only solar production")
CREATE INDEX idx_energy_type ON energy_measurements(measurement_type);
```

**2. Application Deployment**
The core logic resides in a Python script designed for the AppDaemon environment. This script manages the scheduling, state retrieval from Home Assistant, delta calculations, and database transactions.

👉 Download electricity_recorder.py and place it in your apps/ directory.

**3. Configuration**
Configuration is handled entirely via YAML. You can define global defaults (like your main import price) and then override them for specific meters (like your export price). You can add as many meters as needed, including Grid Import/Export, Solar Production, and Battery Charge/Discharge. Do not change the names of the different end points, such as `grid_export` or `battery_discharge`.

👉 Copy the configuration template from apps.yaml.

**4. Security**
It is highly recommended to keep database credentials out of your main configuration files. This application supports Home Assistant's !secret format.

👉 Refer to secrets.yaml for the credential structure.

## How It Works
Every 15 minutes, the script performs the following operations:

1. Snapshots the current cumulative values of all configured meters.
2. Calculates the Delta (usage) since the last run, handling any counter resets automatically.
3. Retrieves Prices for the current interval.
4. Applies Smart Logic: If the grid export meter shows activity, the script checks if that energy came from Solar or Battery. It prioritizes Solar for export first. It then splits the generated energy into "Exported" (valued at Export Price) and "Self-Consumed" (valued at Import Price) to derive the final cost/profit for that specific interval.

**Example:** If you produced 1 kWh of solar, but exported 0.4 kWh to the grid:
  - 0.4 kWh is valued at your Export Price (Profit).
  - 0.6 kWh is valued at your Import Price (Savings/Avoided Cost).
  - The script calculates the weighted average price and records it for that 15-minute block.
  - The same is done for battery export, first assuming that all solar is exported. Thus 1 kWh from solar, 1 kWh from battery and 0.4 kWh export will value all of the solar at export price and part of the battery produce as export price.
5. Commits the normalized data to PostgreSQL.
