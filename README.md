# Cost Explorer MCP Server

Servidor MCP para analizar costes y uso de AWS a traves de la API de AWS Cost Explorer, disenado para **despliegues en Kubernetes con IAM Roles Anywhere** y **asuncion de roles multi-cuenta**.

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
- Gestion de sesiones thread-safe con cache LRU (maximo 1000 sesiones)
- Refresco automatico de tokens 5 minutos antes de su expiracion
- Deduplicacion de peticiones concurrentes (una sola llamada STS por cuenta)

---

## Arquitectura de Autenticacion

### Como funciona

Este servidor esta disenado para ejecutarse en **Kubernetes**, donde el pod obtiene **credenciales base de AWS via IAM Roles Anywhere**, y para **cada peticion**, el servidor asume un rol IAM especifico (cross-account) basado en el `client_id`.

**Flujo de autenticacion:**
1. El pod obtiene credenciales base via IAM Roles Anywhere (sin AWS_PROFILE dentro del contenedor)
2. Se proporciona un fichero `clients.json` con el mapeo: `client_id -> role_arn`
3. Para cada peticion de herramienta:
   - Se envia el `client_id`
   - El servidor busca el `role_arn` en `clients.json`
   - El servidor llama a `sts:AssumeRole` hacia el rol correspondiente
   - Se crea un cliente de Cost Explorer (ce) y se cachea hasta la expiracion del token
   - Los tokens se refrescan automaticamente antes de expirar

### Prerequisitos

- **IAM Roles Anywhere** configurado en tu cuenta AWS
- **Kubernetes** con credenciales de Roles Anywhere montadas
- **Rol base (runner role)**: el rol con el que se ejecuta el pod, con permisos para asumir los roles de cuenta
  - `sts:AssumeRole` sobre cada ARN de rol de cuenta
- **Trust policy** de cada rol de cuenta: permite la asuncion por el rol base (con `ExternalId` si es necesario)

### Gestion de concurrencia

El servidor maneja las peticiones concurrentes de forma segura:
- **Caches thread-safe**: Todas las caches usan locks para prevenir condiciones de carrera
- **Deduplicacion de refresco**: Si multiples peticiones necesitan refrescar el mismo token, solo se realiza una llamada STS
- **Eviccion LRU**: Maximo 1000 sesiones cacheadas con eviccion automatica de las menos usadas

### Permisos IAM necesarios

**Rol de cuenta** (el rol que se asume):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetDimensionValues",
        "ce:GetTags",
        "ce:GetCostForecast",
        "ce:GetCostAndUsageComparisons",
        "ce:GetCostComparisonDrivers"
      ],
      "Resource": "*"
    }
  ]
}
```

**Rol base** (identidad del pod):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::ACCOUNT_ID_1:role/CostExplorerRole",
        "arn:aws:iam::ACCOUNT_ID_2:role/CostExplorerRole"
      ]
    }
  ]
}
```

**Trust policy** en cada rol de cuenta:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::BASE_ACCOUNT_ID:role/BaseRole"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

## Configuracion

### Configuracion multi-cuenta (clients.json)

Crea un fichero `clients.json` y montalo en el contenedor. El servidor utiliza el `client_id` para buscar el rol a asumir.

#### Ejemplo de `clients.json`
```json
{
  "clients": {
    "cuenta-produccion": {
      "role_arn": "arn:aws:iam::ACCOUNT_ID_1:role/CostExplorerRole",
      "account_id": "ACCOUNT_ID_1",
      "account_type": "payer",
      "description": "Cuenta de gestion (payer)"
    },
    "cuenta-desarrollo": {
      "role_arn": "arn:aws:iam::ACCOUNT_ID_2:role/CostExplorerRole",
      "account_id": "ACCOUNT_ID_2",
      "account_type": "linked",
      "payer_id": "cuenta-produccion",
      "description": "Cuenta de desarrollo (linked)"
    }
  }
}
```

### Variables de Entorno

| Variable | Requerida | Valor por defecto | Descripcion |
|----------|-----------|-------------------|-------------|
| `CLIENTS_CONFIG_PATH` | Si | - | Ruta al fichero `clients.json` dentro del contenedor |
| `AWS_REGION` | No | `us-east-1` | Region AWS por defecto |
| `FASTMCP_LOG_LEVEL` | No | `WARNING` | Nivel de logging (ERROR, WARNING, INFO, DEBUG) |
| `VALIDATE_FILTER_VALUES` | No | `false` | Habilitar llamadas AWS para validacion de filtros (ver abajo) |
| `MCP_TRANSPORT` | No | `sse` (contenedor) / `stdio` (local) | Modo de transporte: `stdio`, `sse`, o `streamable-http` |
| `MCP_HOST` | No | `0.0.0.0` (contenedor) / `127.0.0.1` (local) | Host de escucha para transportes SSE/HTTP |
| `MCP_PORT` | No | `8000` | Puerto de escucha para transportes SSE/HTTP |
| `MCP_MOUNT_PATH` | No | - | Ruta de montaje opcional para transporte SSE/HTTP |

---

## Herramientas Disponibles

El servidor MCP de Cost Explorer proporciona las siguientes herramientas:

### Utilidades
| Herramienta | Descripcion |
|-------------|-------------|
| `get_today_date` | Obtiene la fecha actual para determinar datos relevantes |
| `list_active_sessions` | Lista todas las sesiones activas de Cost Explorer (para monitorizar multiples cuentas concurrentes) |
| `close_session` | Cierra una sesion especifica y libera recursos |

### Consultas de Costes
| Herramienta | Descripcion |
|-------------|-------------|
| `get_dimension_values` | Obtiene los valores disponibles para una dimension especifica (ej. SERVICE, REGION) |
| `get_tag_values` | Obtiene los valores disponibles para una clave de etiqueta |
| `get_cost_and_usage` | Recupera datos de costes y uso de AWS con opciones de filtrado y agrupacion |

### Analisis y Comparacion
| Herramienta | Descripcion |
|-------------|-------------|
| `get_cost_and_usage_comparisons` | Compara costes entre dos periodos para identificar cambios y tendencias |
| `get_cost_comparison_drivers` | Analiza que provoco los cambios de coste entre periodos (top 10 factores mas significativos) |
| `get_cost_forecast` | Genera previsiones de costes basadas en patrones de uso historicos |

### Uso Multi-Cuenta

**Importante**: Siempre hay que pasar el `client_id` al invocar una herramienta. El servidor asumira el rol correspondiente de `clients.json`.

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

### Control de la validacion

Por defecto, las validaciones AWS estan **deshabilitadas** para reducir costes. Si se pasa un valor invalido, AWS devolvera un error que el servidor retransmitira.

Para habilitar las validaciones AWS:
```bash
export VALIDATE_FILTER_VALUES=true
```

**Recomendacion**: Mantener deshabilitado (`false`) en produccion. Los valores invalidos resultaran en errores de AWS, lo cual es aceptable y ahorra dinero.

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

El servidor soporta tres modos de transporte:

| Modo | Descripcion | Caso de uso |
|------|-------------|-------------|
| `stdio` | Entrada/salida estandar | Desarrollo local, herramientas CLI |
| `sse` | Server-Sent Events sobre HTTP | **Por defecto en contenedores**, clientes web, MCP Inspector |
| `streamable-http` | Transporte HTTP streamable | Transporte HTTP alternativo |

Para despliegues en contenedores, el servidor usa SSE por defecto escuchando en el puerto 8000.

### Build de Docker

```bash
docker build -t cost-explorer-mcp:latest .
```

### Docker Run (modo SSE - recomendado para contenedores)

```bash
docker run -d \
  -p 8000:8000 \
  -e CLIENTS_CONFIG_PATH=/config/clients.json \
  -e AWS_SHARED_CREDENTIALS_FILE=/home/app/.aws/credentials \
  -e AWS_CONFIG_FILE=/home/app/.aws/config \
  -e FASTMCP_LOG_LEVEL=INFO \
  -v $(pwd)/clients.json:/config/clients.json:ro \
  -v ~/.aws:/home/app/.aws:ro \
  cost-explorer-mcp:latest
```

El servidor estara disponible en `http://localhost:8000/sse`.

### Docker Run (modo stdio - para testing local)

```bash
docker run -it --rm \
  -e MCP_TRANSPORT=stdio \
  -e CLIENTS_CONFIG_PATH=/config/clients.json \
  -e AWS_SHARED_CREDENTIALS_FILE=/home/app/.aws/credentials \
  -e AWS_CONFIG_FILE=/home/app/.aws/config \
  -v $(pwd)/clients.json:/config/clients.json:ro \
  -v ~/.aws:/home/app/.aws:ro \
  cost-explorer-mcp:latest
```

### Probar la conexion SSE

Usando el MCP Inspector:
```bash
npx @modelcontextprotocol/inspector
```
Seleccionar transporte **SSE** y conectar a `http://localhost:8000/sse`.

Usando curl:
```bash
# Deberia devolver el endpoint de sesion
curl -N http://localhost:8000/sse
```

### K3s con IAM Roles Anywhere

Para K3s con IAM Roles Anywhere, montar las credenciales:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cost-explorer-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cost-explorer-mcp
  template:
    metadata:
      labels:
        app: cost-explorer-mcp
    spec:
      containers:
      - name: cost-explorer
        image: cost-explorer-mcp:latest
        ports:
        - containerPort: 8000
          name: http
        env:
        - name: AWS_REGION
          value: "us-east-1"
        - name: CLIENTS_CONFIG_PATH
          value: "/config/clients.json"
        - name: VALIDATE_FILTER_VALUES
          value: "false"
        - name: MCP_TRANSPORT
          value: "sse"
        volumeMounts:
        - name: clients-config
          mountPath: /config
          readOnly: true
        - name: iam-anywhere-creds
          mountPath: /var/run/aws
          readOnly: true
        livenessProbe:
          exec:
            command:
            - /usr/local/bin/docker-healthcheck.sh
          initialDelaySeconds: 10
          periodSeconds: 60
        readinessProbe:
          tcpSocket:
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
      volumes:
      - name: clients-config
        configMap:
          name: cost-explorer-clients
      - name: iam-anywhere-creds
        hostPath:
          path: /var/run/aws
          type: Directory
---
apiVersion: v1
kind: Service
metadata:
  name: cost-explorer-mcp
spec:
  selector:
    app: cost-explorer-mcp
  ports:
  - port: 8000
    targetPort: 8000
    name: http
```

### EKS con IRSA

Para EKS, usar IAM Roles for Service Accounts (IRSA):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cost-explorer-sa
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT_ID:role/CostExplorerBaseRole
```

---

## Tests

### Ejecutar tests unitarios

```bash
# Crear entorno virtual e instalar dependencias
uv sync --dev

# Activar el entorno virtual
source .venv/bin/activate

# Ejecutar todos los tests (487 tests)
pytest tests/ -v -o "addopts="

# Ejecutar tests de autenticacion (56 tests, 96% cobertura)
pytest tests/test_auth_multiclient.py -v -o "addopts="

# Ejecutar con informe de cobertura
pytest tests/ --cov=awslabs/cost_explorer_mcp_server --cov-report=term-missing -o "addopts="
```

### Cobertura de Tests

La suite de tests incluye 487 tests con una cobertura global del 92%, cubriendo todos los handlers, el sistema de autenticacion multi-cuenta, inventario de recursos, analisis de utilizacion y optimizacion de costes:

| Modulo | Cobertura |
|--------|-----------|
| `auth.py` (multi-cuenta) | 86% |
| `cost_explorer/usage.py` | 95% |
| `cost_explorer/comparison.py` | 94% |
| `cost_explorer/forecast.py` | 98% |
| `cost_explorer/trend.py` | 97% |
| `cost_explorer/metadata.py` | 97% |
| `cost_explorer/validation.py` | 96% |
| `cost_explorer/helpers.py` | 98% |
| `cost_explorer/models.py` | 100% |
| `savings.py` | 97% |
| `inventory/ec2.py` | 98% |
| `inventory/ebs.py` | 93% |
| `inventory/elb.py` | 82% |
| `inventory/network.py` | 86% |
| `inventory/rds.py` | 96% |
| `inventory/s3.py` | 79% |
| `utilization/*` | 83-100% |
| `server.py` | 93% |

---

## Ejemplos de Uso

Algunos ejemplos de como usar el servidor MCP de Cost Explorer con consultas en lenguaje natural:

### Ejemplos de analisis de costes

```
Muestrame los costes de AWS de los ultimos 3 meses agrupados por servicio en la region us-east-1
Desglosa los costes de S3 por clase de almacenamiento del primer trimestre de 2025
Muestrame los costes de los recursos de produccion etiquetados con Environment=prod
Cuales fueron mis costes de instancias reservadas vs bajo demanda en mayo?
Cual fue el uso de instancias EC2 por tipo de instancia?
```

### Ejemplos de comparacion de costes

```
Compara mis costes de AWS entre abril y mayo de 2025
Como cambiaron mis costes de EC2 del mes pasado a este mes?
Por que aumento mi factura de AWS en junio comparado con mayo?
Que causo el pico en mis costes de S3 el mes pasado?
```

### Ejemplos de previsiones

```
Haz una prevision de mis costes de AWS para el proximo mes
Predice mi gasto en EC2 para el proximo trimestre
Cual sera mi factura total de AWS para el resto de 2025?
```

---

## Licencia

Este proyecto esta licenciado bajo la Licencia Apache 2.0 - consulta el fichero LICENSE para mas detalles.
