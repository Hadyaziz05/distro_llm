#!/usr/bin/env python3
"""
Auto-scaling script for DistroLLM workers
Run this on a schedule (e.g., every 5 minutes with cron)

Usage:
  python3 autoscale.py --token YOUR_DO_TOKEN --scale-up-at 75 --scale-down-at 25
"""

import os
import sys
import argparse
import json
import subprocess
from typing import List, Dict
import requests
from datetime import datetime

class DigitalOceanAutoscaler:
    def __init__(self, token: str, tag: str = "worker"):
        self.token = token
        self.tag = tag
        self.api_url = "https://api.digitalocean.com/v2"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def get_droplets_by_tag(self) -> List[Dict]:
        """Get all droplets with the worker tag"""
        url = f"{self.api_url}/droplets?tag_name={self.tag}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()["droplets"]
    
    def get_droplet_metrics(self, droplet_id: int) -> Dict:
        """Get CPU and memory metrics for a droplet"""
        url = f"{self.api_url}/monitoring/metrics/droplet/cpu?host_id={droplet_id}&start={int((datetime.now().timestamp() - 300))}&end={int(datetime.now().timestamp())}&step=60"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            if data.get("data", {}).get("values"):
                # Get latest value
                values = [float(v[1]) for v in data["data"]["values"] if v[1]]
                return {"cpu": sum(values) / len(values) if values else 0}
        except Exception as e:
            print(f"⚠️  Error getting metrics for droplet {droplet_id}: {e}")
        
        return {"cpu": 0}
    
    def create_worker_droplet(self) -> Dict:
        """Create a new worker droplet"""
        # Get an existing worker to clone its configuration
        workers = self.get_droplets_by_tag()
        if not workers:
            print("❌ No worker droplets found to clone")
            return None
        
        template_worker = workers[0]
        new_name = f"distrollm-worker-{len(workers) + 1}"
        
        create_payload = {
            "name": new_name,
            "region": template_worker["region"]["slug"],
            "size": template_worker["size_slug"],
            "image": template_worker["image"]["id"],
            "backups": template_worker["backups"],
            "monitoring": True,
            "vpc_uuid": template_worker.get("vpc_uuid"),
            "user_data": open("worker_init.sh").read(),
            "tags": ["worker", "distrollm"]
        }
        
        url = f"{self.api_url}/droplets"
        response = requests.post(url, headers=self.headers, json=create_payload)
        response.raise_for_status()
        
        droplet = response.json()["droplet"]
        print(f"✅ Created new worker: {new_name} (ID: {droplet['id']})")
        return droplet
    
    def destroy_droplet(self, droplet_id: int, name: str) -> bool:
        """Destroy a worker droplet"""
        url = f"{self.api_url}/droplets/{droplet_id}"
        response = requests.delete(url, headers=self.headers)
        
        if response.status_code == 204:
            print(f"✅ Destroyed droplet: {name} (ID: {droplet_id})")
            return True
        else:
            print(f"❌ Failed to destroy droplet {name}")
            return False
    
    def update_load_balancer(self, droplet_ids: List[int]):
        """Update load balancer with new droplet list"""
        # This requires the load balancer ID - get from state or hardcode
        # For now, just print instructions
        print(f"📝 Update load balancer to include droplet IDs: {droplet_ids}")
        print("   Run: terraform apply -auto-approve")

def main():
    parser = argparse.ArgumentParser(description="DistroLLM Auto-scaler")
    parser.add_argument("--token", required=True, help="DigitalOcean API token")
    parser.add_argument("--scale-up-at", type=float, default=75, help="CPU % to scale up")
    parser.add_argument("--scale-down-at", type=float, default=25, help="CPU % to scale down")
    parser.add_argument("--min-workers", type=int, default=2, help="Minimum workers")
    parser.add_argument("--max-workers", type=int, default=4, help="Maximum workers")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually scale")
    
    args = parser.parse_args()
    
    scaler = DigitalOceanAutoscaler(args.token)
    
    print(f"🔍 Checking worker metrics at {datetime.now().isoformat()}")
    workers = scaler.get_droplets_by_tag()
    print(f"📊 Found {len(workers)} workers")
    
    if not workers:
        print("❌ No workers found!")
        return 1
    
    # Get average CPU across all workers
    cpu_values = []
    for worker in workers:
        metrics = scaler.get_droplet_metrics(worker["id"])
        cpu_values.append(metrics["cpu"])
        print(f"  {worker['name']}: {metrics['cpu']:.1f}% CPU")
    
    avg_cpu = sum(cpu_values) / len(cpu_values) if cpu_values else 0
    print(f"\n📈 Average CPU: {avg_cpu:.1f}%")
    
    # Scale up
    if avg_cpu > args.scale_up_at and len(workers) < args.max_workers:
        print(f"⬆️  CPU {avg_cpu:.1f}% > threshold {args.scale_up_at}% → Scaling up")
        if not args.dry_run:
            scaler.create_worker_droplet()
        else:
            print("   [DRY RUN] Would create new worker")
    
    # Scale down
    elif avg_cpu < args.scale_down_at and len(workers) > args.min_workers:
        print(f"⬇️  CPU {avg_cpu:.1f}% < threshold {args.scale_down_at}% → Scaling down")
        if not args.dry_run:
            # Remove the last worker
            worker_to_remove = workers[-1]
            scaler.destroy_droplet(worker_to_remove["id"], worker_to_remove["name"])
        else:
            print(f"   [DRY RUN] Would remove {workers[-1]['name']}")
    
    else:
        print("✅ No scaling needed")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
