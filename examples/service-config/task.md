# Service config

Write `config.json` for the payments API, to be deployed behind the load balancer.

- Name: `payments-api`.
- It listens on port 8080 and answers health checks on `/healthz`.
- It must survive one instance restarting.
- Connections should time out after half a second, requests after three seconds.
- Normal logging; nothing chatty in production.

Check your work with `{{check}}` and correct it until nothing fails.
