# DistroLLM Deployment Guide

Complete guide to deploying DistroLLM on DigitalOcean with Terraform.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Internet / Load Balancer                      │
│              (80/443 → worker:8000)                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ┌────▼────┐         ┌────▼────┐         ┌────▼────┐
   │ Worker 1 │         │ Worker 2 │         │ Worker 3 │
   │ (s-1vcpu │         │ (s-1vcpu │         │ (s-1vcpu │
   │  -2gb)   │         │  -2gb)   │         │  -2gb)   │
   │ :8000    │         │ :8000    │         │ :8000    │
   └────┬─────┘         └────┬─────┘         └────┬─────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                    VPC (Private Network)
                             │
                        ┌────▼────┐
                        │  Redis   │
                        │ (s-2vcpu │
                        │  -4gb)   │
                        │ :6379    │
                        └──────────┘
```

## Quick Start

### 1. Prerequisites

```bash
# Install Terraform (macOS)
brew tap hashicorp/tap
brew install hashicorp/tap/terraform

# Or download from: https://www.terraform.io/downloads

# Verify installation
terraform --version

# Create DigitalOcean API token:
# https://cloud.digitalocean.com/account/api/tokens/new
```

### 2. Configure Terraform

```bash
cd terraform

# Copy example config
cp terraform.tfvars.example terraform.tfvars

# Edit with your DigitalOcean token
# Open terraform.tfvars in your editor and paste your API token
```

### 3. Deploy Infrastructure

```bash
# Initialize Terraform
terraform init

# Review the plan
terraform plan

# Apply (creates all resources)
terraform apply
# Type 'yes' when prompted

# Wait 2-3 minutes for droplets to initialize
```

### 4. Get Access Information

```bash
# View all outputs
terraform output

# Get specific values
LOAD_BALANCER_IP=$(terraform output -raw load_balancer_ip)
REDIS_IP=$(terraform output -raw redis_private_ip)

echo "API endpoint: http://$LOAD_BALANCER_IP"
echo "Redis private IP: $REDIS_IP"
```

## How It Works

### Configuration Flow

1. **Terraform deploys infrastructure**:
   - Redis droplet (with docker-compose)
   - 3 worker droplets (with docker-compose)
   - Load balancer
   - VPC + Firewall

2. **Worker initialization** (`worker_init.sh`):
   - Installs Docker
   - Creates docker-compose.yml with:
     - `REDIS_URL` = Redis private IP
     - `QDRANT_URL` = Your cloud Qdrant instance
     - `QDRANT_API_KEY` = Your API key
   - Starts container

3. **config.py reads environment**:
   - Loads `.env` file (if exists)
   - Reads `REDIS_URL` from environment
   - Workers connect to Redis via private IP

### Key Environment Variables

**Set by Terraform (in worker_init.sh)**:
```
REDIS_URL=redis://:PASSWORD@REDIS_PRIVATE_IP:6379/0
REDIS_STREAM_KEY=inference_queue
QDRANT_URL=https://ec4e3824-8050-4914-93a3-fa9634463833.us-west-1-0.aws.cloud.qdrant.io:6333
QDRANT_API_KEY=eyJhbGc...
QDRANT_COLLECTION=my_docs
```

**config.py defaults** (if not set):
```python
REDIS_URL = "redis://localhost:6379"  # Falls back to localhost
```

## Testing Deployment

### 1. Test Load Balancer

```bash
LB_IP=$(terraform output -raw load_balancer_ip)

# Simple health check
curl http://$LB_IP/health

# Test API
curl -X POST http://$LB_IP/build-context \
  -H "Content-Type: application/json" \
  -d '{"query": "What are best practices for distributed systems?"}'
```

### 2. SSH into Droplets

```bash
# Get worker IP
WORKER_IP=$(terraform output -json worker_droplets | jq -r '.[0].public_ip' | head -1)

# Connect
ssh root@$WORKER_IP

# Check container status
docker ps
docker-compose logs -f worker

# Verify Redis connection
curl -s http://localhost:8000/health

# Check Docker stats
docker stats
```

### 3. SSH into Redis

```bash
REDIS_IP=$(terraform output -raw redis_private_ip)
REDIS_PASS=$(terraform output -raw redis_password)

# From a worker droplet:
redis-cli -h $REDIS_IP -a $REDIS_PASS INFO stats
```

## Scaling

### Manual Scaling Up

```bash
# Edit terraform.tfvars
# Change: initial_worker_count = 3 → initial_worker_count = 4

terraform apply

# Wait 1-2 minutes for new droplet to boot
```

### Manual Scaling Down

```bash
# Edit terraform.tfvars
# Change: initial_worker_count = 3 → initial_worker_count = 2

terraform apply

# Confirms the droplet will be destroyed
```

### Automatic Scaling

Use the provided Python script for automatic scaling:

```bash
# Install dependencies on monitoring droplet
pip3 install requests

# Make script executable
chmod +x autoscale.py

# Test dry-run
python3 autoscale.py \
  --token $DO_TOKEN \
  --scale-up-at 75 \
  --scale-down-at 25 \
  --dry-run

# Add to crontab (runs every 5 minutes)
*/5 * * * * /usr/bin/python3 /opt/autoscale.py \
  --token $DO_TOKEN \
  --scale-up-at 75 \
  --scale-down-at 25 \
  >> /var/log/autoscale.log 2>&1
```

## Customization

### Change Droplet Size

Edit `terraform.tfvars`:
```
worker_size = "s-2vcpu-4gb"  # Larger workers
redis_size  = "s-4vcpu-8gb"  # Larger Redis
```

Then apply:
```bash
terraform apply
```

### Add HTTPS

1. Create certificate in DigitalOcean console
2. Get certificate ID
3. Update `terraform.tfvars`:
   ```
   certificate_id = "your-cert-id-here"
   ```
4. Apply: `terraform apply`

### Change Regions

```bash
# Available regions: sfo3, nyc1, lon1, blr1, sgp1, tor1, ams3, fra1
terraform apply -var="region=nyc1"
```

## Monitoring

### View Real-time Logs

```bash
# Log into worker
ssh root@$WORKER_IP

# Follow logs
docker-compose logs -f worker

# Watch CPU/memory
watch docker stats
```

### Monitor Redis

```bash
# SSH into any droplet
# Connect to Redis CLI
redis-cli -h $REDIS_IP -a $REDIS_PASS

# Check queue depth
XLEN inference_queue

# Monitor operations
MONITOR

# View memory usage
INFO memory
```

### DigitalOcean Console

1. Go to https://cloud.digitalocean.com
2. Click "Droplets" to see your instances
3. Click on a droplet to view:
   - CPU/memory graphs
   - Bandwidth usage
   - Recent actions

## Troubleshooting

### Workers can't connect to Redis

1. Check Redis is running:
   ```bash
   ssh root@$REDIS_IP
   docker ps
   ```

2. Verify password in worker env:
   ```bash
   ssh root@$WORKER_IP
   docker-compose ps
   docker-compose logs worker | grep REDIS
   ```

3. Check firewall allows port 6379:
   ```bash
   doctl compute firewall list
   doctl compute firewall get distrollm-worker-firewall
   ```

### Load balancer shows "unhealthy"

1. Check worker health endpoint:
   ```bash
   curl http://$WORKER_IP:8000/health
   ```

2. Check logs for errors:
   ```bash
   docker-compose logs worker | grep -i error
   ```

3. Verify port 8000 is open:
   ```bash
   netstat -tlnp | grep 8000
   ```

### High memory usage

1. Check Docker container limits:
   ```bash
   docker inspect worker | grep Memory
   ```

2. Check Redis memory:
   ```bash
   redis-cli -a $REDIS_PASS INFO memory
   ```

3. Reduce stream size in config:
   ```
   REDIS_STREAM_MAXLEN=5000  # Instead of 10000
   ```

## Cost Optimization

### Current Monthly Cost

- 3 workers (s-1vcpu-2gb @ $12/mo): $36
- 1 Redis (s-2vcpu-4gb @ $24/mo): $24
- Load balancer: $10
- **Total: ~$70/month**

### Ways to Reduce

1. **Use smaller droplets** (start with 512MB/1CPU):
   ```
   worker_size = "s-512mb-1gb"  # $6/mo
   redis_size  = "s-1vcpu-2gb"  # $12/mo
   ```

2. **Use shared CPU droplets** (up to 50% savings):
   - Change `s-1vcpu-2gb` to `s-1vcpu-2gb-shared`

3. **Start with fewer workers**:
   ```
   initial_worker_count = 1
   max_worker_count = 3
   ```

4. **Remove backups**:
   ```
   # In terraform: backups = false
   ```

## Cleanup

### Destroy Everything

```bash
# View what will be destroyed
terraform plan -destroy

# Confirm and destroy
terraform destroy

# Type 'yes' when prompted
```

### Keep Redis, Destroy Workers

```bash
# Remove workers only
terraform destroy -target 'digitalocean_droplet.workers'
```

## Advanced Topics

### Custom Docker Image

Update `terraform.tfvars`:
```
worker_image = "your-registry/your-image:tag"
```

### Private Docker Registry

Add to `worker_init.sh`:
```bash
# Login to registry before pulling
docker login -u $REGISTRY_USER -p $REGISTRY_PASS
docker pull $REGISTRY_URL/image:tag
```

### Monitoring Stack

Deploy Prometheus + Grafana on monitoring droplet:
```bash
# In monitoring droplet docker-compose.yml
services:
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
  
  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
```

## FAQ

**Q: How do I update the worker image?**
A: Push new image to registry, then:
```bash
ssh root@$WORKER_IP
docker pull your-image:latest
docker-compose up -d --force-recreate
```

**Q: Can I use DigitalOcean Kubernetes (DOKS)?**
A: Yes! DOKS supports native auto-scaling via Horizontal Pod Autoscaler. See DigitalOcean docs.

**Q: How do I backup Redis data?**
A: Redis volume has AOF enabled. Snapshots are in `/data/appendonly.aof`.

**Q: Can I add a database?**
A: Yes, add a PostgreSQL droplet to docker-compose and link containers.

## Support

- Terraform Docs: https://registry.terraform.io/providers/digitalocean/digitalocean/latest/docs
- DigitalOcean Docs: https://docs.digitalocean.com
- Troubleshooting: Check logs in `/var/log/autoscale.log` on monitoring droplet
