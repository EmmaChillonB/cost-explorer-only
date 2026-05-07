# Cost Explorer MCP Server

Servidor MCP para analizar costes y uso de AWS a traves de la API de AWS Cost Explorer, disenado para **despliegues en EKS con Pod Identity** y **asuncion de roles multi-cuenta**.

## Tabla de Contenidos

- [Funcionalidades](#funcionalidades)
- [Arquitectura de Autenticacion](#arquitectura-de-autenticacion)
- [Configuracion](#configuracion)
- [Validacion y Optimizacion de Costes](#validacion-y-optimizacion-de-costes)
- [Herramientas Disponibles](#herramientas-disponibles)
- [Despliegue](#despliegue)
- [Tests](#tests)
- [Ejemplos de Uso](#ejemplos-de-uso)
- [Licencia](#licencia)

---

## Funcionalidades

### Analisis de costes y uso de AWS
- Desglose detallado de costes por servicio, region y otras dimensiones
- Consulta de datos historicos de costes para periodos de tiempo especificos
- Filtrado de costes por dimensiones, etiquetas y categorias de coste

### Comparacion de costes entre periodos
- Uso de la funcionalidad de [Cost Comparison](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-cost-comparison.html) de AWS Cost Explorer
- Comparacion de costes entre dos periodos para identificar cambios y tendencias
- Analisis de los principales factores de coste (top 10) para entender incrementos/decrementos

### Prevision de costes futuros
- Generacion de previsiones basadas en patrones de uso historicos
- Predicciones con intervalos de confianza (80% o 95%)
- Granularidad de prevision diaria y mensual

### Arquitectura multi-cuenta
- Soporte para multiples cuentas AWS con diferentes roles IAM
- Gestion de sesiones thread-safe con cache LRU (maximo 300 sesiones)
- Refresco automatico de tokens 5 minutos antes de su expiracion
- Deduplicacion de peticiones concurrentes (una sola llamada STS por cuenta)

---

## Arquitectura de Autenticacion

### Como funciona

Este servidor esta disenado para ejecutarse en **EKS**, donde el pod obtiene credenciales via **EKS Pod Identity**, y para cada peticion asume un rol IAM especifico (cross-account) basado en el `client_id`.

**Flujo de autenticacion:**
1. El pod obtiene credenciales base via EKS Pod Identity
2. La configuracion de clientes se carga desde **AWS Secrets Manager** (en produccion) o desde un fichero `clients.json` local (en desarrollo)
3. Para cada peticion de herramienta:
   - Se envia el `client_id`
   - El servidor busca el `role_arn` en la configuracion de clientes
   - El servidor llama a `sts:AssumeRole` hacia el rol correspondiente
   - Se crea un cliente de Cost Explorer (ce) y se cachea hasta la expiracion del token
   - Los tokens se refrescan automaticamente antes de expirar

### Carga de configuracion de clientes

El servidor prioriza las fuentes en este orden:

1. **AWS Secrets Manager** — si la variable `CLIENTS_CONFIG_SECRET` esta definida, carga la configuracion desde el secret indicado. La configuracion se cachea en memoria durante 5 minutos (configurable via `CLIENTS_CONFIG_CACHE_TTL`). Cuando se actualiza el secret, el servidor detecta el cambio automaticamente en el siguiente ciclo de cache sin necesidad de reinicio.

2. **Fichero local** — si `CLIENTS_CONFIG_SECRET` no esta definida, busca `clients.json` en la ruta indicada por `CLIENTS_CONFIG_PATH` o en ubicaciones por defecto relativas al modulo. El fichero se recarga automaticamente cuando cambia (deteccion por mtime).

### Prerequisitos

- **EKS** con Pod Identity habilitado
- **Rol base (pod role)**: el rol IAM asignado al pod via Pod Identity, con permisos para:
  - `sts:AssumeRole` sobre cada ARN de rol de cuenta
  - `secretsmanager:GetSecretValue` sobre el secret de configuracion
- **Rol en cada cuenta target**: permite la asuncion por el rol base

### Gestion de concurrencia

El servidor maneja las peticiones concurrentes de forma segura:
- **Caches thread-safe**: Todas las caches usan locks para prevenir condiciones de carrera
- **Deduplicacion de refresco**: Si multiples peticiones necesitan refrescar el mismo token, solo se realiza una llamada STS
- **Eviccion LRU**: Maximo 300 sesiones cacheadas con eviccion automatica de las menos usadas

### Permisos IAM necesarios

**Rol base** (identidad del pod via Pod Identity):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::ACCOUNT_ID_1:role/mcp-cost-optimizer",
        "arn:aws:iam::ACCOUNT_ID_2:role/mcp-cost-optimizer"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:iam::BASE_ACCOUNT_ID:secret:mcp-cost-optimizer/clients"
    }
  ]
}
```

**Rol en cada cuenta target** (`mcp-cost-optimizer`):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "ce:GetDimensionValues",
        "ce:GetTags",
        "ce:GetCostAndUsageComparisons",
        "ce:GetCostComparisonDrivers",
        "ce:GetReservationUtilization",
        "ce:GetReservationCoverage",
        "ce:GetReservationPurchaseRecommendation",
        "ce:GetSavingsPlansCoverage",
        "ce:GetSavingsPlansUtilization",
        "ce:GetSavingsPlansPurchaseRecommendation",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "ec2:DescribeRegions",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeSnapshots",
        "ec2:DescribeNatGateways",
        "ec2:DescribeAddresses",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTargetGroups",
        "elasticloadbalancing:DescribeTargetHealth",
        "rds:DescribeDBInstances",
        "rds:DescribeDBRecommendations",
        "s3:ListAllMyBuckets",
        "s3:GetBucketLocation",
        "s3:GetBucketLifecycleConfiguration",
        "s3:GetBucketVersioning"
      ],
      "Resource": "*"
    }
  ]
}
```

**Trust policy** en cada rol de cuenta target:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::BASE_ACCOUNT_ID:role/mcp-cost-optimizer-CLUSTER_ID"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

## Configuracion

### Configuracion multi-cuenta (clients.json)

El servidor utiliza el `client_id` para buscar el rol a asumir. La configuracion puede estar en AWS Secrets Manager o en un fichero local.

#### Ejemplo de configuracion
```json
{
  "clients": {
    "cuenta-produccion": {
      "role_arn": "arn:aws:iam::ACCOUNT_ID_1:role/mcp-cost-optimizer",
      "account_id": "ACCOUNT_ID_1",
      "account_type": "payer",
      "description": "Cuenta de gestion (payer)"
    },
    "cuenta-desarrollo": {
      "role_arn": "arn:aws:iam::ACCOUNT_ID_2:role/mcp-cost-optimizer",
      "account_id": "ACCOUNT_ID_2",
      "account_type": "linked",
      "payer_id": "cuenta-produccion",
      "description": "Cuenta de desarrollo (linked)"
    }
  }
}
```

### Actualizar la configuracion en produccion

```bash
aws secretsmanager put-secret-value \
  --secret-id "mcp-cost-optimizer/clients" \
  --secret-string file://clients.json \
  --region eu-west-1 --profile <profile>
```

El servidor detecta el cambio automaticamente en los proximos 5 minutos sin reinicio.

### Variables de Entorno

| Variable | Requerida | Valor por defecto | Descripcion |
|----------|-----------|-------------------|-------------|
| `CLIENTS_CONFIG_SECRET` | No* | - | ARN o nombre del secret en AWS Secrets Manager con la configuracion de clientes |
| `CLIENTS_CONFIG_PATH` | No* | - | Ruta al fichero `clients.json` (alternativa local a Secrets Manager) |
| `CLIENTS_CONFIG_CACHE_TTL` | No | `300` | Segundos de cache para la configuracion de clientes desde Secrets Manager |
| `AWS_REGION` | No | `us-east-1` | Region AWS por defecto |
| `FASTMCP_LOG_LEVEL` | No | `WARNING` | Nivel de logging (ERROR, WARNING, INFO, DEBUG) |
| `VALIDATE_FILTER_VALUES` | No | `false` | Habilitar llamadas AWS para validacion de filtros |
| `MCP_TRANSPORT` | No | `stdio` | Modo de transporte: `stdio`, `sse`, o `streamable-http` |
| `MCP_HOST` | No | `0.0.0.0` (contenedor) | Host de escucha para transportes SSE/HTTP |
| `MCP_PORT` | No | `8000` | Puerto de escucha para transportes SSE/HTTP |
| `MCP_MOUNT_PATH` | No | - | Ruta de montaje opcional para transporte SSE/HTTP |

*Se requiere uno de los dos: `CLIENTS_CONFIG_SECRET` (produccion) o `CLIENTS_CONFIG_PATH` / fichero local (desarrollo).

---

## Herramientas Disponibles

El servidor MCP de Cost Explorer proporciona las siguientes herramientas:

### Utilidades
| Herramienta | Descripcion |
|-------------|-------------|
| `get_today_date` | Obtiene la fecha actual para determinar datos relevantes |
| `list_active_sessions` | Lista todas las sesiones activas de Cost Explorer |
| `close_session` | Cierra una sesion especifica y libera recursos |

### Consultas de Costes
| Herramienta | Descripcion |
|-------------|-------------|
| `get_dimension_values` | Obtiene los valores disponibles para una dimension especifica (ej. SERVICE, REGION) |
| `get_tag_values` | Obtiene los valores disponibles para una clave de etiqueta |
| `get_cost_and_usage` | Recupera datos de costes y uso de AWS con opciones de filtrado y agrupacion |
| `get_cost_trend_with_anomalies` | Tendencia de 6 meses + deteccion de anomalias en una sola llamada |

### Analisis y Comparacion
| Herramienta | Descripcion |
|-------------|-------------|
| `get_cost_and_usage_comparisons` | Compara costes entre dos periodos para identificar cambios y tendencias |
| `get_cost_comparison_drivers` | Analiza que provoco los cambios de coste entre periodos (top 10 factores) |
| `get_cost_forecast` | Genera previsiones de costes basadas en patrones de uso historicos |
| `get_savings_commitments` | Savings Plans y Reserved Instances actuales, cobertura y utilizacion |

### Inventario
| Herramienta | Descripcion |
|-------------|-------------|
| `describe_ec2_instances` | Informacion compacta de instancias EC2 |
| `list_ec2_regions_with_instances` | Regiones con instancias (running/stopped) |
| `describe_ebs_volumes` | Volumenes EBS con deteccion de huerfanos |
| `describe_ebs_snapshots` | Snapshots EBS con deteccion de huerfanos |
| `describe_rds_instances` | Instancias RDS |
| `describe_load_balancers` | Load Balancers |
| `describe_nat_gateways` | NAT Gateways |
| `describe_elastic_ips` | Elastic IPs |
| `list_s3_buckets` | Buckets S3 con flags de lifecycle |

### Utilizacion
| Herramienta | Descripcion |
|-------------|-------------|
| `get_ec2_utilization` | Utilizacion de CPU de instancias EC2 via CloudWatch |
| `get_rds_utilization` | Utilizacion de instancias RDS |
| `get_ebs_utilization` | Utilizacion de volumenes EBS |
| `get_elb_utilization` | Utilizacion de Load Balancers |
| `get_nat_gateway_utilization` | Utilizacion de NAT Gateways |
| `get_multi_resource_utilization` | EC2 + RDS agrupados por buckets de utilizacion |

### Uso Multi-Cuenta

**Importante**: Siempre hay que pasar el `client_id` al invocar una herramienta. El servidor asumira el rol correspondiente de la configuracion de clientes.

#### Ejemplo: `get_cost_and_usage`
```json
{
  "client_id": "cuenta-produccion",
  "date_range": {
    "start_date": "2026-01-01",
    "end_date": "2026-01-31"
  },
  "granularity": "MONTHLY",
  "group_by": "SERVICE",
  "metric": "UnblendedCost"
}
```

---

## Validacion y Optimizacion de Costes

### Como funciona la validacion

Cada llamada a la API de Cost Explorer cuesta **$0.01**. El servidor realiza validaciones en las peticiones antes de enviarlas a AWS.

**Validaciones locales (siempre se ejecutan, sin llamadas a AWS):**
- Formato de fecha (YYYY-MM-DD)
- Logica de rango de fechas (inicio antes que fin)
- Restricciones por granularidad (ej. HOURLY maximo 14 dias)
- Validacion de claves de dimension (SERVICE, REGION, etc.)
- Validacion de estructura de filtros (operadores And, Or, Not)
- Validacion de agrupacion (group by)

**Validaciones AWS (opcionales, deshabilitadas por defecto):**
- Validar que los valores de dimension existen (ej. "Amazon EC2" es un valor valido de SERVICE)
- Validar que los valores de etiqueta existen

### Estimacion de costes

| Operacion | Llamadas API | Coste |
|-----------|-------------|-------|
| Consulta simple de costes | 1 | $0.01 |
| Consulta con filtro (validacion deshabilitada) | 1 | $0.01 |
| Consulta con filtro (validacion habilitada) | 2+ | $0.02+ |
| Comparacion de costes | 2 | $0.02 |
| Prevision de costes | 1 | $0.01 |

---

## Despliegue

### Modos de Transporte

| Modo | Descripcion | Caso de uso |
|------|-------------|-------------|
| `stdio` | Entrada/salida estandar | Desarrollo local, herramientas CLI |
| `sse` | Server-Sent Events sobre HTTP | Clientes web, MCP Inspector |
| `streamable-http` | Transporte HTTP streamable | **Recomendado para EKS** |

### Desarrollo local

```bash
# Instalar dependencias
uv sync

# Ejecutar con fichero local
CLIENTS_CONFIG_PATH=./clients.json uv run cost-optimizer
```

### Build y push a ECR

```bash
aws ecr get-login-password --region eu-west-1 --profile <profile> \
  | docker login --username AWS --password-stdin \
    675492141136.dkr.ecr.eu-west-1.amazonaws.com

docker build -t mcp-costoptimizer-aws .
docker tag mcp-costoptimizer-aws:latest \
  675492141136.dkr.ecr.eu-west-1.amazonaws.com/mcp-costoptimizer-aws:latest
docker push \
  675492141136.dkr.ecr.eu-west-1.amazonaws.com/mcp-costoptimizer-aws:latest
```

### Despliegue en EKS (via Terraform/Terragrunt)

El despliegue en EKS se gestiona desde el repo `innovation-account`. Requiere:

1. Crear el secret en Secrets Manager (una vez):
```bash
aws secretsmanager create-secret \
  --name "mcp-cost-optimizer/clients" \
  --secret-string file://clients.json \
  --region eu-west-1 --profile <profile>
```

2. Crear manualmente el rol `mcp-cost-optimizer` en cada cuenta target con los permisos y trust policy indicados en [Permisos IAM necesarios](#permisos-iam-necesarios).

3. Aplicar la infra:
```bash
cd config/eu-west-1/envs/cost-optimizer
terragrunt run-all apply
```

El MCP quedara disponible internamente en el cluster en:
```
http://mcp.cost-optimizer.svc.cluster.local:8000
```

---

## Tests

### Ejecutar tests unitarios

```bash
uv sync --dev
source .venv/bin/activate
pytest tests/ -v -o "addopts="
```

---

## Ejemplos de Uso

### Analisis de costes

```
Muestrame los costes de AWS de los ultimos 3 meses agrupados por servicio
Desglosa los costes de S3 por clase de almacenamiento del primer trimestre de 2026
Cuales fueron mis costes de instancias reservadas vs bajo demanda en mayo?
```

### Comparacion de costes

```
Compara mis costes de AWS entre abril y mayo de 2026
Por que aumento mi factura de AWS en junio comparado con mayo?
Que causo el pico en mis costes de S3 el mes pasado?
```

### Previsiones

```
Haz una prevision de mis costes de AWS para el proximo mes
Cual sera mi factura total de AWS para el resto de 2026?
```

---

## Licencia

Este proyecto esta licenciado bajo la Licencia Apache 2.0 - consulta el fichero LICENSE para mas detalles.
