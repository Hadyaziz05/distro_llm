import redis
import os

# Replace these with your actual values or 
# export them as environment variables
REDIS_URL="redis://:F07o9Cq46v7JSowL@164.92.98.121:6379/0"
STREAM_KEY = "inference_queue"

def test_redis_connection():
    try:
        print(f"--- Attempting to connect to: {REDIS_URL.split('@')[-1]} ---")
        
        # Initialize client
        r = redis.from_url(REDIS_URL, socket_timeout=5)
        
        # 1. Test ba
        # sic connectivity
        if r.ping():
            print("✅ Connectivity: Success! (PONG)")
        
        # 2. Test Stream metadata
        # This checks if the stream exists or is accessible
        try:
            groups = r.xinfo_groups(STREAM_KEY)
            print(f"✅ Stream '{STREAM_KEY}': Accessible. Found {len(groups)} consumer groups.")
        except redis.exceptions.ResponseError:
            print(f"ℹ️  Stream '{STREAM_KEY}': Not found yet (this is normal if no data has been sent).")

        # 3. Test a dummy write (Optional - remove if you don't want to pollute the queue)
        # test_id = r.xadd(STREAM_KEY, {"test": "connection_check"})
        # print(f"✅ Write Test: Success! Message ID: {test_id}")

    except redis.exceptions.AuthenticationError:
        print("❌ Error: Authentication failed. Check your password.")
    except redis.exceptions.ConnectionError:
        print("❌ Error: Could not connect to host. Check IP, Port, and Firewall/Whitelist.")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")

if __name__ == "__main__":
    test_redis_connection()