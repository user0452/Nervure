class RetryPolicy:

    def can_retry(self, task):
        return task.attempts <= task.max_retries
