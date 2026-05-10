# DistroLLM Terraform Infrastructure

This directory contains Terraform configuration to deploy DistroLLM to DigitalOcean with:
- 3 worker droplets running Docker containers
- 1 Redis droplet for queue management
- Load balancer distributing traffic
- Auto-scaling capability (2-4 workers based on CPU/memory)
- VPC for internal networking
- Firewall rules for security

## Prerequisites

1. **DigitalOcean Account** - https://digitalocean.com
2. **DigitalOcean API Token** - https://cloud.digitalocean.com/account/api/tokens
3. **Terraform** - v1.0+ (install from https://www.terraform.io/downloads.html)
4. **Python 3** - for auto-scaling script

## Setup

### 1. Configure Terraform Variables

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your DigitalOcean token
```

### 2. Initialize Terraform

```bash
terraform init
```

### 3. Review the Plan

```bash
terraform plan
```

### 4. Apply Configuration

```bash
terraform apply
```

This will:
- Create Redis droplet
- Create 3 worker droplets
- Set up load balancer
- Configure VPC and firewall
- Output the load balancer IP and Redis private IP

## Outputs

After deployment, Terraform outputs:
- **Load Balancer IP**: Public IP to access your API
- **Redis Private IP**: Used by workers to connect to queue
- **Redis Password**: Sensitive - stored in state file

```bash
# View outputs
terraform output
terraform output load_balancer_ip
```

## Configuration

### Worker Configuration

Workers receive these environment variables via `user_data`:
- `REDIS_URL`: Connection string with password
- `QDRANT_URL`: Your cloud Qdrant instance
- `QDRANT_API_KEY`: Qdrant authentication

### Update config.py

The worker Docker image uses config.py which now reads `REDIS_URL`:

```python
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
```

This is automatically set to the Redis droplet's private IP by the init script.

## Auto-Scaling

DigitalOcean doesn't have native auto-scaling like AWS. Use the provided script:

### Manual Scaling

```bash
# Check current workers
terraform state list | grep worker

# Add worker
terraform apply -var="initial_worker_count=4"

# Remove worker (edit terraform.tfvars and apply)
terraform apply
```

### Automatic Scaling with Cron

1. **Set up monitoring droplet**:
   ```bash
   doctl compute droplet create monitoring \
     --region sfo3 \
     --size s-1vcpu-1gb \
     --image ubuntu-22-04-x64
   ```

2. **Install dependencies**:
   ```bash
   sudo apt-get install python3-pip
   pip3 install requests
   ```

3. **Deploy autoscale.py**:
   ```bash
   scp autoscale.py root@monitoring_droplet_ip:/opt/
   ```

4. **Set up cron job**:
   ```bash
   # SSH into monitoring droplet
   ssh root@monitoring_droplet_ip
   
   # Edit crontab
   crontab -e
   
   # Add this line (runs every 5 minutes)
   */5 * * * * /usr/bin/python3 /opt/autoscale.py \
     --token $DO_TOKEN \
     --scale-up-at 75 \
     --scale-down-at 25 \
     --min-workers 2 \
     --max-workers 4 \
     >> /var/log/autoscale.log 2>&1
   ```

5. **Test dry-run**:
   ```bash
   python3 autoscale.py \
     --token YOUR_TOKEN \
     --dry-run \
     --scale-up-at 75
   ```

## Load Balancer Configuration

The load balancer:
- Listens on port 80 (HTTP) and 443 (HTTPS, if certificate configured)
- Forwards to workers on port 8000
- Health checks: `/health` endpoint every 10 seconds
- Sticky sessions: Disabled (stateless API)

### Add HTTPS

1. Create SSL certificate in DigitalOcean
2. Get certificate ID
3. Update `terraform.tfvars`:
   ```
   certificate_id = "your-cert-id"
   ```
4. Apply: `terraform apply`

## Monitoring

### Access Worker Logs

```bash
# SSH into a worker
ssh root@worker_public_ip

# View container logs
docker-compose logs -f worker
```

### Monitor Load Balancer

```bash
# Check health status
curl http://load_balancer_ip/health

# View metrics in DigitalOcean console
# or use doctl:
doctl compute load-balancer get distrollm-lb
```

### Monitor Redis

```bash
# Connect to Redis
redis-cli -h redis_private_ip -a $REDIS_PASSWORD
redis-cli> INFO stats
```

## Scaling Thresholds

Adjust in `terraform.tfvars`:
- `cpu_threshold_up`: 75% (scale up when exceeded)
- `cpu_threshold_down`: 25% (scale down when below)
- `memory_threshold_up`: 80% (future: add memory-based scaling)

## Cleanup

To destroy all resources:

```bash
terraform destroy
```

⚠️  **Warning**: This will delete all droplets, the load balancer, and VPC. Data in Redis volumes will be lost.

To keep a resource:
```bash
terraform destroy -target digitalocean_droplet.workers
```

## Troubleshooting

### Workers not connecting to Redis

1. Check Redis is running:
   ```bash
   ssh root@redis_ip
   docker-compose -f docker-compose-redis.yml ps
   ```

2. Check worker logs:
   ```bash
   ssh root@worker_ip
   docker-compose logs worker
   ```

3. Verify Redis password in user_data matches config

### Load balancer not routing traffic

1. Check health status:
   ```bash
   doctl compute load-balancer get distrollm-lb
   ```

2. Check worker health endpoint:
   ```bash
   curl http://worker_public_ip:8000/health
   ```

3. Verify firewall allows port 8000 from load balancer

### Auto-scaler not working

1. Check monitoring droplet:
   ```bash
   ssh root@monitoring_ip
   python3 autoscale.py --token $DO_TOKEN --dry-run
   ```

2. Check cron logs:
   ```bash
   tail -f /var/log/autoscale.log
   ```

3. Verify API token has write permissions

## Cost Estimation

With default configuration:
- 3 worker droplets (s-1vcpu-2gb): ~$18/month each = $54
- 1 Redis droplet (s-2vcpu-4gb): ~$24/month
- Load balancer: ~$10/month
- **Total: ~$88/month**

To reduce costs:
- Use smaller droplet sizes
- Start with 1-2 workers instead of 3
- Use dedicated spaces for backups instead of droplet backups

## Next Steps

1. ✅ Deploy with `terraform apply`
2. ✅ Test with `curl http://load_balancer_ip/build-context`
3. ✅ Set up monitoring/auto-scaling
4. ✅ Configure HTTPS with certificate
5. ✅ Set up backups and disaster recovery
