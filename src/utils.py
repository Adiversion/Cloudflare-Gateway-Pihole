import hashlib
import os
import re
import json
import http.client
from src import ids_pattern, CACHE_FILE
from src.cloudflare import get_lists, get_rules, get_list_items


class GithubAPI:
    BASE_URL = "api.github.com"
    GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
    HEADERS = {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Mozilla/5.0"
    }

    @staticmethod
    def request(method, url, body=None):
        conn = http.client.HTTPSConnection(GithubAPI.BASE_URL)
        conn.request(method, url, body, headers=GithubAPI.HEADERS)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return json.loads(data) if data else {}

    @staticmethod
    def delete(url):
        return GithubAPI.request("DELETE", url)

    @staticmethod
    def get(url):
        return GithubAPI.request("GET", url)


def load_cache():
    try:
        if is_running_in_github_actions():
            workflow_status, completed_run_ids = get_latest_workflow_status()
            delete_completed_workflows(completed_run_ids)
            if workflow_status == "success":
                if os.path.exists(CACHE_FILE):
                    with open(CACHE_FILE, "r") as file:
                        return json.load(file)
        elif os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as file:
                return json.load(file)
    except json.JSONDecodeError:
        return {"lists": [], "rules": [], "mapping": {}}
    return {"lists": [], "rules": [], "mapping": {}}


def save_cache(cache):
    with open(CACHE_FILE, "w") as file:
        json.dump(cache, file)


def get_current_lists(cache, list_name):
    # Filter from cache by prefix (cache may contain both block and allow lists)
    cached = [l for l in cache["lists"] if l["name"].startswith(list_name)]
    if cached:
        return cached
    current_lists = get_lists(list_name)
    # Merge into cache without overwriting other prefix entries
    existing_ids = {l["id"] for l in cache["lists"]}
    for l in current_lists:
        if l["id"] not in existing_ids:
            cache["lists"].append(l)
    save_cache(cache)
    return current_lists


def get_current_rules(cache, rule_name):
    cached = [r for r in cache["rules"] if r["name"].startswith(rule_name)]
    if cached:
        return cached
    current_rules = get_rules(rule_name)
    existing_ids = {r["id"] for r in cache["rules"]}
    for r in current_rules:
        if r["id"] not in existing_ids:
            cache["rules"].append(r)
    save_cache(cache)
    return current_rules


def get_list_items_cached(cache, list_id):
    if list_id in cache["mapping"]:
        return cache["mapping"][list_id]
    items = get_list_items(list_id)
    cache["mapping"][list_id] = items
    save_cache(cache)
    return items


def compute_domain_hash(domains):
    """Deterministic hash of an iterable of domains. Used to detect whether
    the desired domain set changed since the last successful run, so the
    sync can be skipped entirely instead of diffing every list."""
    return hashlib.sha256("\n".join(sorted(domains)).encode()).hexdigest()


def get_cached_domain_state(cache, prefix):
    """Return (hash, cached_domain_set) for a given list prefix."""
    entry = cache.setdefault("domain_hashes", {}).get(prefix)
    if entry is None:
        return None, set()
    return entry["hash"], set(entry["domains"])


def set_cached_domain_state(cache, prefix, domains):
    cache.setdefault("domain_hashes", {})[prefix] = {
        "hash": compute_domain_hash(domains),
        "domains": sorted(domains),
    }

def get_domain_diff(new_domains, cached_domains):
    """Compute the exact add/remove sets between new and cached domains."""
    new_set = set(new_domains)
    to_add = new_set - cached_domains
    to_remove = cached_domains - new_set
    return to_add, to_remove


def get_cached_reverse_mapping(cache, prefix):
    """Return {domain: list_id} reverse mapping for a prefix."""
    return cache.get("reverse_mappings", {}).get(prefix, {})


def set_cached_reverse_mapping(cache, prefix, mapping):
    """Store {domain: list_id} reverse mapping for a prefix."""
    cache.setdefault("reverse_mappings", {})[prefix] = mapping


def build_reverse_mapping(cache, list_ids, prefix):
    """Build {domain: list_id} from the forward mapping (list_id -> [domains])
    for lists belonging to the given prefix."""
    rev = {}
    mapping = cache.get("mapping", {})
    cached_lists = cache.get("lists", [])
    prefix_lists = {lst["id"] for lst in cached_lists if lst["name"].startswith(prefix)}
    for lst_id in list_ids:
        if lst_id in prefix_lists and lst_id in mapping:
            for domain in mapping[lst_id]:
                rev[domain] = lst_id
    return rev


def safe_sort_key(list_item):
    match = re.search(r"\d+", list_item["name"])
    return int(match.group()) if match else float("inf")


def extract_list_ids(rule):
    if not rule or not rule.get("traffic"):
        return set()
    return set(ids_pattern.findall(rule["traffic"]))


def delete_completed_workflows(completed_run_ids):
    if completed_run_ids:
        for run_id in completed_run_ids:
            delete_url = f"/repos/{GithubAPI.GITHUB_REPOSITORY}/actions/runs/{run_id}"
            GithubAPI.delete(delete_url)


def get_latest_workflow_status():
    WORKFLOW_RUNS_URL = (
        f"/repos/{GithubAPI.GITHUB_REPOSITORY}/actions/runs?per_page=5"
    )
    runs_data = GithubAPI.get(WORKFLOW_RUNS_URL).get("workflow_runs", [])
    completed_runs = [run for run in runs_data if run["status"] == "completed"]

    if completed_runs:
        latest_run = completed_runs[0]
        completed_run_ids = [run["id"] for run in completed_runs]
        return latest_run["conclusion"], completed_run_ids

    return None, []


def is_running_in_github_actions():
    return os.getenv("GITHUB_ACTIONS") == "true"


def delete_cache():
    CACHE_URL = f"/repos/{GithubAPI.GITHUB_REPOSITORY}/actions/caches"
    caches = GithubAPI.get(CACHE_URL).get("actions_caches", [])
    for cache_id in [cache["id"] for cache in caches]:
        GithubAPI.delete(f"{CACHE_URL}/{cache_id}")
