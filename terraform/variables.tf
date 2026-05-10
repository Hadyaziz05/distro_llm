variable "digitalocean_token" {
  description = "DigitalOcean API token"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "DigitalOcean region"
  type        = string
  default     = "sfo3"
}

variable "worker_size" {
  description = "Droplet size for workers"
  type        = string
  default     = "s-1vcpu-2gb"
}

variable "redis_size" {
  description = "Droplet size for Redis"
  type        = string
  default     = "s-2vcpu-4gb"
}

variable "worker_image" {
  description = "Docker image for workers"
  type        = string
  default     = "hadyaziz05/worker-context-builder:latest"
}

variable "initial_worker_count" {
  description = "Initial number of worker droplets"
  type        = number
  default     = 3
}

variable "min_worker_count" {
  description = "Minimum worker droplets"
  type        = number
  default     = 2
}

variable "max_worker_count" {
  description = "Maximum worker droplets"
  type        = number
  default     = 4
}

variable "cpu_threshold_up" {
  description = "CPU percentage to scale up"
  type        = number
  default     = 75
}

variable "cpu_threshold_down" {
  description = "CPU percentage to scale down"
  type        = number
  default     = 25
}

variable "memory_threshold_up" {
  description = "Memory percentage to scale up"
  type        = number
  default     = 80
}

variable "certificate_id" {
  description = "DigitalOcean certificate ID for HTTPS (optional)"
  type        = string
  default     = ""
}

variable "ssh_keys" {
  description = "List of SSH key IDs or fingerprints to add to worker droplets"
  type        = list(string)
}

variable "certificate_name" {
  description = "Name of the DigitalOcean certificate for HTTPS"
  type        = string
  default     = ""
}

variable "admin_cidr" {
  description = "Your admin IP or CIDR for SSH access, e.g. 203.0.113.5/32"
  type        = string
}