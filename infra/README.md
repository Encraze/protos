# Local infra

Run `make dev` from the repo root.

## Services

| Service  | URL                       | Credentials             |
|----------|---------------------------|-------------------------|
| API      | http://localhost:8000     | —                       |
| Web      | http://localhost:8080     | —                       |
| Postgres | localhost:5432            | protos / protos         |
| Redis    | localhost:6379            | —                       |
| MinIO    | http://localhost:9001     | protos / protosprotos   |

The api container connects to postgres at `postgres:5432`, redis at `redis:6379`, and minio at `minio:9000` over the compose network.
