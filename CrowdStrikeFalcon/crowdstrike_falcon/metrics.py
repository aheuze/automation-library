from prometheus_client import Counter, Histogram

# Declare prometheus metrics
prom_namespace = "symphony_module_crowdstrike"

INCOMING_DETECTIONS = Counter(
    name="collected_detections",
    documentation="Number of detections collected from Crowdstrike",
    namespace=prom_namespace,
    labelnames=["intake_key"],
)

INCOMING_VERTICLES = Counter(
    name="collected_verticles",
    documentation="Number of detections collected from Crowdstrike",
    namespace=prom_namespace,
    labelnames=["intake_key"],
)

OUTCOMING_EVENTS = Counter(
    name="forwarded_events",
    documentation="Number of events forwarded to SEKOIA.IO",
    namespace=prom_namespace,
    labelnames=["intake_key"],
)

EVENTS_LAG = Histogram(
    name="event_lags",
    documentation="The delay, in seconds, from the date of the last event",
    namespace=prom_namespace,
    labelnames=["intake_key", "stream"],
)