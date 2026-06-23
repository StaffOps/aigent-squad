"""Edge gateway package (spec 31).

A thin FastAPI front door in front of the supervisor: edge auth, protocol
translation (native /query + OpenAI /v1/*), admission control (worker pool +
backpressure), and forwarding to the supervisor backend. Holds NO orchestration
logic — routing/fan-out/investigation live only in the supervisor.
"""

__version__ = "0.1.0"
