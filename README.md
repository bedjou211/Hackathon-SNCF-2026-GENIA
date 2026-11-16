# Hackathon SNCF 2026 - Equipe 4

Monitoring temps reel du reseau SNCF Ile-de-France (IDFM) avec calcul de KPIs de performance.

## Structure du projet

```
.
├── README.md
├── sql/
│   ├── 01_setup/                          # Creation des objets Snowflake
│   │   ├── 01_tables_gtfs.sql             # Tables referentiel GTFS (stops, trips, stop_times)
│   │   ├── 02_tables_monitoring.sql       # Tables monitoring, historique, refs, disruptions, KPI
│   │   ├── 03_tables_external.sql         # Tables meteo et evenements
│   │   └── 04_network_integrations.sql    # Network rules, secrets, external access integrations
│   │
│   ├── 02_procedures/                     # Stored procedures Python
│   │   ├── 01_proc_load_monitoring.sql    # Ingestion temps reel SNCF via API IDFM
│   │   ├── 02_proc_compute_kpis.sql       # Calcul des 4 KPIs unifies
│   │   ├── 03_proc_load_disruptions.sql   # Chargement disruptions + enrichissement GTFS
│   │   ├── 04_proc_load_weather.sql       # Meteo via Open-Meteo
│   │   └── 05_proc_load_events.sql        # Evenements via PredictHQ
│   │
│   └── 03_tasks/                          # Scheduling
│       └── 01_tasks.sql                   # Toutes les tasks Snowflake
│
└── python/
    └── load_disruptions_legacy.py         # Script Python legacy (Snowpark notebook)
```

## Ordre d'execution

1. `sql/01_setup/01_tables_gtfs.sql`
2. `sql/01_setup/02_tables_monitoring.sql`
3. `sql/01_setup/03_tables_external.sql`
4. `sql/01_setup/04_network_integrations.sql`
5. `sql/02_procedures/01_proc_load_monitoring.sql`
6. `sql/02_procedures/02_proc_compute_kpis.sql`
7. `sql/02_procedures/03_proc_load_disruptions.sql`
8. `sql/02_procedures/04_proc_load_weather.sql`
9. `sql/02_procedures/05_proc_load_events.sql`
10. `sql/03_tasks/01_tasks.sql`

## KPIs calcules

| KPI | Description |
|-----|-------------|
| KPI1 | Taux de suppression de trains |
| KPI2 | Retard moyen au depart |
| KPI3 | Ratio temps d'attente reel vs theorique |
| KPI4 | Ratio temps de stationnement en gare |

## Sources de donnees

- **IDFM API** : monitoring temps reel + disruptions
- **Open-Meteo** : conditions meteo Ile-de-France
- **PredictHQ** : evenements majeurs (concerts, sports, etc.)
- **GTFS** : referentiel arrets et lignes
