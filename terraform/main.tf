# ──────────────────────────────────────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_tag" "worker" {
  name = "distrollm-worker"
}

resource "digitalocean_tag" "app" {
  name = "distrollm"
}

resource "digitalocean_tag" "redis" {
  name = "distrollm-redis"
}

resource "digitalocean_tag" "lb" {
  name = "distrollm-lb"
}

# ──────────────────────────────────────────────────────────────────────────────
# SSH Key
# ──────────────────────────────────────────────────────────────────────────────

resource "digitalocean_ssh_key" "default" {
  name       = "distrollm-key"
  public_key = file(var.ssh_public_key_path)
}

# ──────────────────────────────────────────────────────────────────────────────
# VPC
# ──────────────────────────────────────────────────────────────────────────────

data "digitalocean_vpc" "main" {
  region = var.region
}

# ──────────────────────────────────────────────────────────────────────────────
# Redis
# ──────────────────────────────────────────────────────────────────────────────

resource "random_password" "redis_password" {
  length  = 16
  special = false
}

resource "digitalocean_droplet" "redis" {
  name       = "distrollm-redis"
  region     = var.region
  size       = var.redis_size
  image      = "ubuntu-22-04-x64"
  backups    = true
  monitoring = true
  vpc_uuid   = data.digitalocean_vpc.main.id
  tags       = [digitalocean_tag.redis.name]
  ssh_keys   = [digitalocean_ssh_key.default.fingerprint]

  user_data = templatefile("${path.module}/redis_init.sh", {
    redis_password = random_password.redis_password.result
  })

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [digitalocean_tag.redis]
}

resource "digitalocean_firewall" "redis" {
  name = "distrollm-redis-firewall"
  tags = [digitalocean_tag.redis.name]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = [var.admin_cidr]
  }

  inbound_rule {
    protocol    = "tcp"
    port_range  = "6379"
    source_tags = [digitalocean_tag.worker.name]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "6379"
    source_addresses = var.allowed_ips
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

  depends_on = [
    digitalocean_droplet.redis,
    digitalocean_tag.worker,
    digitalocean_tag.redis,
  ]
}

# ──────────────────────────────────────────────────────────────────────────────
# Workers
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
    size     = var.worker_size
    region   = var.region
    image    = "ubuntu-22-04-x64"
    vpc_uuid = data.digitalocean_vpc.main.id
    tags     = [digitalocean_tag.worker.name, digitalocean_tag.app.name]
    ssh_keys = [digitalocean_ssh_key.default.fingerprint]

    user_data = templatefile("${path.module}/worker_init.sh", {
      worker_image   = var.worker_image
      redis_host     = digitalocean_droplet.redis.ipv4_address_private
      redis_port     = "6379"
      redis_password = random_password.redis_password.result
      allowed_ips    = var.allowed_ips
    })
  }

  depends_on = [
    digitalocean_droplet.redis,
    digitalocean_tag.worker,
    digitalocean_tag.app,
  ]
}

data "digitalocean_droplets" "workers" {
  filter {
    key    = "tags"
    values = [digitalocean_tag.worker.name]
  }

  depends_on = [digitalocean_droplet_autoscale.workers]
}

resource "digitalocean_firewall" "workers" {
  name = "distrollm-worker-firewall"
  tags = [digitalocean_tag.worker.name]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = [var.admin_cidr]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = [data.digitalocean_vpc.main.ip_range]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = var.allowed_ips
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

  depends_on = [
    digitalocean_droplet_autoscale.workers,
    digitalocean_tag.worker,
    digitalocean_tag.lb,
  ]
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
    target_port     = 80
  }

  http_idle_timeout_seconds = 1800

  healthcheck {
    protocol                 = "http"
    port                     = 80
    path                     = "/health"
    check_interval_seconds   = 10
    response_timeout_seconds = 5
    healthy_threshold        = 3
    unhealthy_threshold      = 3
  }

  sticky_sessions {
    type = "none"
  }

  droplet_tag = digitalocean_tag.app.name

  depends_on = [
    digitalocean_droplet_autoscale.workers,
    digitalocean_tag.app,
  ]
}

resource "digitalocean_droplet" "management" {
  name       = "distrollm-management"
  region     = var.region
  size       = "s-2vcpu-4gb"
  image      = "ubuntu-22-04-x64"
  backups    = false
  monitoring = true
  vpc_uuid   = data.digitalocean_vpc.main.id
  tags       = [digitalocean_tag.app.name]
  ssh_keys   = [digitalocean_ssh_key.default.fingerprint]

  user_data = <<-EOF
    #!/bin/bash
    set -e

    apt-get update -y
    apt-get install -y build-essential libssl-dev git

    # Build wrk from source
    git clone https://github.com/wg/wrk.git /opt/wrk
    cd /opt/wrk && make
    cp /opt/wrk/wrk /usr/local/bin/wrk
    chmod +x /usr/local/bin/wrk

    # Create reusable load-test helper script
    cat > /usr/local/bin/loadtest <<'SCRIPT'
    #!/bin/bash
    TARGET=$${1:-"http://localhost"}
    CONNECTIONS=$${2:-1000}
    DURATION=$${3:-30s}
    THREADS=$${4:-12}

    echo "========================================"
    echo " Load Test Report"
    echo " Target:      $TARGET"
    echo " Connections: $CONNECTIONS"
    echo " Duration:    $DURATION"
    echo " Threads:     $THREADS"
    echo "========================================"

    wrk -t$THREADS -c$CONNECTIONS -d$DURATION --latency $TARGET
    SCRIPT

    chmod +x /usr/local/bin/loadtest
  EOF

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    digitalocean_tag.app,
    digitalocean_ssh_key.default,
  ]
}