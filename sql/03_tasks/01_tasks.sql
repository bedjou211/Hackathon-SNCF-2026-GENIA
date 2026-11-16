USE WAREHOUSE HACKATHON_WH;

-- =============================================
-- TASK 1 : Ingestion monitoring (60s)
-- =============================================
ALTER TASK IF EXISTS TASK_LOAD_IDFM_ALL_SNCF SUSPEND;
ALTER TASK IF EXISTS TASK_COMPUTE_KPIS SUSPEND;

CREATE OR REPLACE TASK TASK_LOAD_IDFM_ALL_SNCF
  WAREHOUSE = HACKATHON_WH
  SCHEDULE = '60 SECOND'
AS
  CALL LOAD_IDFM_STOP_MONITORING_ALL_SNCF();

-- =============================================
-- TASK 2 : Calcul KPIs (chaine apres TASK 1)
-- =============================================
CREATE OR REPLACE TASK TASK_COMPUTE_KPIS
  WAREHOUSE = HACKATHON_WH
  AFTER TASK_LOAD_IDFM_ALL_SNCF
AS
  CALL COMPUTE_ALL_KPIS();

-- =============================================
-- TASK 3 : Meteo (toutes les 30 min)
-- =============================================
CREATE OR REPLACE TASK TASK_LOAD_WEATHER
  WAREHOUSE = HACKATHON_WH
  SCHEDULE = 'USING CRON */30 * * * * Europe/Paris'
AS
  CALL LOAD_CURRENT_WEATHER(48.8566, 2.3522);

-- =============================================
-- TASK 4 : Evenements PredictHQ (toutes les 30 min)
-- =============================================
CREATE OR REPLACE TASK TASK_LOAD_EVENTS
  WAREHOUSE = HACKATHON_WH
  SCHEDULE = 'USING CRON */30 * * * * Europe/Paris'
AS
  CALL LOAD_PREDICTHQ_EVENTS();

-- =============================================
-- ACTIVATION
-- =============================================
ALTER TASK TASK_COMPUTE_KPIS RESUME;
ALTER TASK TASK_LOAD_IDFM_ALL_SNCF RESUME;
ALTER TASK TASK_LOAD_WEATHER RESUME;
ALTER TASK TASK_LOAD_EVENTS RESUME;
