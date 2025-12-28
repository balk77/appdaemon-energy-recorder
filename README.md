Electricity Recorder (AppDaemon)
Electricity Recorder is a sophisticated AppDaemon application designed for Home Assistant users who require precise, granular control over their energy data. By leveraging a PostgreSQL database, this tool records electricity consumption, production, and costs every 15 minutes, providing a solid foundation for advanced energy dashboards (e.g., Grafana) and detailed financial ROI analysis.

Unlike standard energy integrations, this recorder applies logic to determine the true value of your solar and battery energy (Avoided Cost vs. Export Revenue) based on real-time grid interaction.

Key Features
Precision Interval Recording: Captures data exactly on the quarter-hour (00, 15, 30, 45), ensuring alignment with standard utility billing periods and preventing data drift.
Smart Financial Logic:
Avoided Cost Calculation: Automatically values self-consumed solar/battery energy at the import rate (savings).
Export Revenue: Values energy sent to the grid at your specific export tariff.
Weighted Averages: When a 15-minute block contains both self-consumption and export, the system calculates a precise weighted average price per kWh.
Resilient Architecture:
Robust error handling for sensor unavailability (prevents zero-spikes).
Automatic detection of daily meter resets to ensure continuity of cumulative counters.
State restoration after restart to prevent data gaps.
Flexible Configuration: Supports any number of meters via simple YAML configuration, allowing for specific price overrides per meter (e.g., distinct Import and Export tariffs).
Installation & Setup
1. Database Initialization
Before running the application, you must initialize your PostgreSQL database schema. The system uses a normalized table structure to store all measurement types (grid, solar, battery) in a single, efficient table.

👉 See create_table.sql for the required SQL commands.

2. Application Deployment
The core logic resides in a Python script designed for the AppDaemon environment. This script manages the scheduling, state retrieval from Home Assistant, delta calculations, and database transactions.

👉 Download electricity_recorder.py and place it in your apps/ directory.

3. Configuration
Configuration is handled entirely via YAML. You can define global defaults (like your main import price) and then override them for specific meters (like your export price). You can add as many meters as needed, including Grid Import/Export, Solar Production, and Battery Charge/Discharge.

👉 Copy the configuration template from apps.yaml.

4. Security
It is highly recommended to keep database credentials out of your main configuration files. This application supports Home Assistant's !secret format.

👉 Refer to secrets.yaml for the credential structure.

How It Works
Every 15 minutes, the script performs the following operations:

Snapshots the current cumulative values of all configured meters.
Calculates the Delta (usage) since the last run, handling any counter resets automatically.
Retrieves Prices for the current interval.
Applies Smart Logic: If the grid export meter shows activity, the script checks if that energy came from Solar or Battery. It prioritizes Solar for export first. It then splits the generated energy into "Exported" (valued at Export Price) and "Self-Consumed" (valued at Import Price) to derive the final cost/profit for that specific interval.
Commits the normalized data to PostgreSQL.
