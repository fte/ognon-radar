from locust import HttpUser, task, between

class APIUser(HttpUser):
    wait_time = between(1, 5)

    @task
    def health_check(self):
        self.client.get("/api/v1/health")
