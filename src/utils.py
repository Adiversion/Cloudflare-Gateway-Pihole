import os
import json
import http.client
import re
from src import ids_pattern, CACHE_FILE
from src.cloudflare import get_lists, get_rules, get_list_items


class GithubAPI:
    BASE_URL = "api.github.com"
    REPO = os.getenv('GITHUB_REPOSITORY')
    HEADERS = {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Mozilla/5.0"
    }

    @staticmethod
    def request(method, url, body=None):
        """Handles API requests with error handling and response parsing."""
        conn = http.client.HTTPSConnection(GithubAPI.BASE_URL)
        try:
            conn.request(method, url, body, headers=GithubAPI.HEADERS)
            response = conn.getresponse()
            data = response.read()
            return json.loads(data) if data else {}
        except Exception as e:
            print(f"Error during API request: {e}")
            return {}
        finally:
            conn.close()

    @classmethod
    def delete(cls, url):
        return cls.request("DELETE", url)

    @classmethod
    def get(cls, url):
        return cls.request("GET", url)


def load_cache():
    """Loads cache if running in GitHub Actions and previous workflow succeeded."""
    try:
        if is_running_in_github_actions():
            workflow_status, completed_run_ids = get_latest_workflow_status()
            if workflow_status == 'success':
                delete_completed_workflows(completed_run_ids)
                return read_cache_file()
        return read_cache_file()
    except json.JSONDecodeError:
        return default_cache()


def read_cache_file():
    """Reads the cache file if it exists, otherwise returns a default cache."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as file:
            return json.load(file)
    return default_cache()


def default_cache():
    """Returns the default cache structure."""
    return {"lists": [], "rules": [], "mapping": {}}


def save_cache(cache):
    """Writes cache data to a file."""
    with open(CACHE_FILE, 'w') as file:
        json.dump(cache, file)


def get_cached_data(cache, key, fetch_function, *args):
    """Retrieves cached data or fetches it from API if not available."""
    if cache.get(key):
        return cache[key]
    cache[key] = fetch_function(*args)
    save_cache(cache)
    return cache[key]


def get_current_lists(cache, list_name):
    return get_cached_data(cache, "lists", get_lists, list_name)


def get_current_rules(cache, rule_name):
    return get_cached_data(cache, "rules", get_rules, rule_name)


def get_list_items_cached(cache, list_id):
    return get_cached_data(cache["mapping"], list_id, get_list_items, list_id)


def safe_sort_key(list_item):
    """Sorts list items by extracting numeric values."""
    match = re.search(r'\d+', list_item.get("name", ""))
    return int(match.group()) if match else float('inf')


def extract_list_ids(rule):
    """Extracts list IDs from rule traffic."""
    return set(ids_pattern.findall(rule.get('traffic', ''))) if rule else set()


def delete_completed_workflows(completed_run_ids):
    """Deletes completed GitHub Actions workflows."""
    for run_id in completed_run_ids or []:
        GithubAPI.delete(f"/repos/{GithubAPI.REPO}/actions/runs/{run_id}")


def get_latest_workflow_status():
    """Retrieves the latest workflow run status and completed run IDs."""
    url = f"/repos/{GithubAPI.REPO}/actions/runs?per_page=5"
    runs = GithubAPI.get(url).get('workflow_runs', [])

    completed_runs = [run for run in runs if run.get('status') == 'completed']
    if completed_runs:
        return completed_runs[0].get('conclusion'), [run['id'] for run in completed_runs]

    return None, []


def is_running_in_github_actions():
    """Checks if script is running inside GitHub Actions."""
    return os.getenv('GITHUB_ACTIONS') == 'true'


def delete_cache(completed_run_ids=None):
    """Deletes cached GitHub Actions data."""
    cache_url = f"/repos/{GithubAPI.REPO}/actions/caches"
    caches = GithubAPI.get(cache_url).get('actions_caches', [])

    for cache in caches:
        GithubAPI.delete(f"{cache_url}/{cache['id']}")

    if completed_run_ids:
        delete_completed_workflows(completed_run_ids)
