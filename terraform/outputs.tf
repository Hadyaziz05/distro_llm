output "redis_droplet_ip" {
  value       = digitalocean_droplet.redis.ipv4_address
  description = "Redis droplet public IP"
}

output "redis_password" {
  value       = random_password.redis_password.result
  sensitive   = true
  description = "Redis password"
}

output "worker_autoscale_pool" {
  description = "Autoscale pool resource for workers"
  value = {
    id   = digitalocean_droplet_autoscale.workers.id
    name = digitalocean_droplet_autoscale.workers.name
  }
}

output "load_balancer_ip" {
  value       = digitalocean_loadbalancer.main.ip
  description = "Load balancer public IP"
}

output "load_balancer_domain" {
  value       = digitalocean_loadbalancer.main.name
  description = "Load balancer domain name"
}

output "digitalocean_loadbalancer" {
  value       = digitalocean_loadbalancer.main
  description = "Complete DigitalOcean load balancer resource"
}

output "vpc_id" {
  value       = data.digitalocean_vpc.main.id
  description = "VPC ID"
}

output "worker_ips" {
  value       = data.digitalocean_droplets.workers.droplets.*.ipv4_address
  description = "The public IP addresses of the worker droplets"
}

output "worker_private_ips" {
  value       = data.digitalocean_droplets.workers.droplets.*.ipv4_address_private
  description = "The private IP addresses of the worker droplets"
}

output "next_steps" {
  value = <<-EOT
    ✅ Infrastructure deployed successfully!
    
    📊 Monitoring & Auto-Scaling:
    To enable auto-scaling, you have several options:
    
    1. Using a monitoring script (recommended for development):
       - Set up Prometheus on a monitoring droplet
       - Create a custom scaling script that checks metrics
       - Run it on a schedule (e.g., every 5 minutes)
    
    2. Using Kubernetes (production-ready):
       - Migrate workers to DigitalOcean App Platform or DOKS (managed Kubernetes)
       - Deploy Horizontal Pod Autoscaler (HPA)
    
    3. Using Orbiter (third-party):
       - Configure group-based scaling policies
       - Automatic scale up/down based on metrics
    
    📡 Application endpoints:
    - Load Balancer: http://${digitalocean_loadbalancer.main.ip}
    - Redis: ${digitalocean_droplet.redis.ipv4_address_private}:6379
    
    🔧 Configuration:
    - Redis password: (check terraform state or run: terraform output redis_password)
    - Worker count: ${var.initial_worker_count} (can scale to ${var.max_worker_count})
    
    ⚠️  IMPORTANT: Update your config.py to use the Redis IP from Terraform output
  EOT
}
