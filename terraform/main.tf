# ──────────────────────────────────────────────────────────────────────────────
# SSH Key
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_ssh_key" "default" {
  name       = "distrollm-key"
  public_key = file(var.ssh_public_key_path)
}

# ──────────────────────────────────────────────────────────────────────────────
# VPC for networking
# ──────────────────────────────────────────────────────────────────────────────

# resource "digitalocean_vpc" "main" {
#   name        = "distrollm-vpc"
#   region      = var.region
#   description = "VPC for DistroLLM infrastructure"
# }
data "digitalocean_vpc" "main" {
  region = var.region
}
# ──────────────────────────────────────────────────────────────────────────────
# Redis Droplet
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_droplet" "redis" {
  name       = "distrollm-redis"
  region     = var.region
  size       = var.redis_size
  image      = "ubuntu-22-04-x64"
  backups    = true
  monitoring = true
  # vpc_uuid   = data.digitalocean_vpc.main.id
  tags       = ["redis", "queue"]
  ssh_keys   = [digitalocean_ssh_key.default.fingerprint]

  user_data = templatefile("${path.module}/redis_init.sh", {
    redis_password = random_password.redis_password.result
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Random password for Redis
resource "random_password" "redis_password" {
  length  = 16
  special = false
}

# ──────────────────────────────────────────────────────────────────────────────
# Worker Autoscale Pool
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_droplet_autoscale" "workers" {
  name = "distrollm-workers"

  config {
    min_instances          = var.initial_worker_count
    max_instances          = var.max_worker_count
    target_cpu_utilization = 0.6
    cooldown_minutes       = 5
  }

  droplet_template {
    size      = var.worker_size
    region    = var.region
    image     = "ubuntu-22-04-x64"
    vpc_uuid  = data.digitalocean_vpc.main.id
    tags      = ["worker", "distrollm"]
    ssh_keys  = [digitalocean_ssh_key.default.fingerprint]
    user_data = templatefile("${path.module}/worker_init.sh", {
      worker_image   = var.worker_image
      redis_host     = digitalocean_droplet.redis.ipv4_address_private
      redis_port     = "6379"
      redis_password = random_password.redis_password.result
    })
  }

  depends_on = [digitalocean_droplet.redis]
}

data "digitalocean_droplets" "workers" {
  filter {
    key    = "tags"
    values = ["worker", "distrollm"]
  }

  # This ensures the data source refreshes after the autoscale pool is created
  depends_on = [digitalocean_droplet_autoscale.workers]
}

# ──────────────────────────────────────────────────────────────────────────────
# Firewall Rules
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_firewall" "workers" {
  name = "distrollm-worker-firewall"

  droplet_ids = [digitalocean_droplet.redis.id]
  tags        = ["worker", "distrollm"]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = [var.admin_cidr]
  }

  inbound_rule {
    protocol    = "tcp"
    port_range  = "8000"
    source_addresses = ["0.0.0.0/0"]
  }

  inbound_rule {
    protocol    = "tcp"
    port_range  = "6379"
    source_tags = ["worker", "redis"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  depends_on = [digitalocean_droplet_autoscale.workers]
}

# ──────────────────────────────────────────────────────────────────────────────
# Load Balancer
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_loadbalancer" "main" {
  name     = "distrollm-lb"
  region   = var.region
  vpc_uuid = data.digitalocean_vpc.main.id

  forwarding_rule {
    entry_protocol  = "http"
    entry_port      = 80
    target_protocol = "http"
    target_port     = 8000
  }


  healthcheck {
    protocol                 = "http"
    port                     = 8000
    path                     = "/health"
    check_interval_seconds   = 10
    response_timeout_seconds = 5
    healthy_threshold        = 3
    unhealthy_threshold      = 3
  }

  sticky_sessions {
    type = "none"
  }

  droplet_tag = "distrollm"

  depends_on = [digitalocean_droplet_autoscale.workers]
}
