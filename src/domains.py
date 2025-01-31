import os
import http.client
from urllib.parse import urlparse, urljoin
from configparser import ConfigParser
from src import info, convert, silent_error, error
from src.requests import retry, retry_config, RateLimitException, HTTPException


class DomainConverter:
    def __init__(self):
        """Initialize with URL mapping and environment variables."""
        self.env_file_map = {
            "ADLIST_URLS": "./lists/adlist.ini",
            "WHITELIST_URLS": "./lists/whitelist.ini",
            "DYNAMIC_BLACKLIST": "./lists/dynamic_blacklist.txt",
            "DYNAMIC_WHITELIST": "./lists/dynamic_whitelist.txt"
        }
        self.adlist_urls = self.read_urls("ADLIST_URLS")
        self.whitelist_urls = self.read_urls("WHITELIST_URLS")

    def read_urls_from_file(self, filename):
        """Read URLs from a file, falling back to plain text if INI parsing fails."""
        urls = []
        try:
            config = ConfigParser()
            config.read(filename)
            for section in config.sections():
                for key in config.options(section):
                    if not key.startswith("#"):
                        urls.append(config.get(section, key))
        except Exception:
            # Fallback to plain text reading if INI reading fails
            with open(filename, "r") as file:
                urls = [url.strip() for url in file if not url.startswith("#") and url.strip()]
        return urls

    def read_urls_from_env(self, env_var):
        """Read URLs from an environment variable."""
        urls = os.getenv(env_var, "")
        return [url.strip() for url in urls.split() if url.strip()]

    def read_urls(self, env_var):
        """Read URLs from both file and environment variable."""
        file_path = self.env_file_map[env_var]
        urls = self.read_urls_from_file(file_path)
        urls += self.read_urls_from_env(env_var)
        return urls

    @retry(**retry_config)
    def download_file(self, url):
        """Download file from the provided URL, handling redirects and errors."""
        print(f"Trying to download: {url}")
        parsed_url = urlparse(url)
        conn_class = http.client.HTTPSConnection if parsed_url.scheme == "https" else http.client.HTTPConnection
        conn = conn_class(parsed_url.netloc)
        headers = {'User-Agent': 'Mozilla/5.0'}

        conn.request("GET", parsed_url.path, headers=headers)
        response = conn.getresponse()

        # Handle redirection responses
        while response.status in (301, 302, 303, 307, 308):
            location = response.getheader('Location')
            if not location:
                break
            # Construct new absolute URL if relative path is returned
            location = urljoin(url, location) if not urlparse(location).netloc else location
            url = location
            parsed_url = urlparse(url)
            conn = conn_class(parsed_url.netloc)
            conn.request("GET", parsed_url.path, headers=headers)
            response = conn.getresponse()

        # Raise error for non-200 status codes
        if response.status != 200:
            error_message = f"Failed to download file from {url}, status code: {response.status}"
            silent_error(error_message)
            conn.close()
            if response.status == 429:
                raise RateLimitException(error_message)
            else:
                raise HTTPException(error_message)

        # Read response data and close connection
        data = response.read().decode('utf-8')
        conn.close()
        info(f"Downloaded file from {url}. File size: {len(data)}")
        return data

    def process_urls(self):
        """Process all adlist and whitelist URLs and dynamic lists."""
        block_content = self._download_urls(self.adlist_urls)
        white_content = self._download_urls(self.whitelist_urls)

        # Read additional dynamic lists
        block_content += self._read_dynamic_list("DYNAMIC_BLACKLIST", self.env_file_map["DYNAMIC_BLACKLIST"])
        white_content += self._read_dynamic_list("DYNAMIC_WHITELIST", self.env_file_map["DYNAMIC_WHITELIST"])

        # Convert collected content into a domain list
        return convert.convert_to_domain_list(block_content, white_content)

    def _download_urls(self, urls):
        """Helper method to download content for a list of URLs."""
        content = ""
        for url in urls:
            content += self.download_file(url)
        return content

    def _read_dynamic_list(self, env_var, file_path):
        """Helper method to read dynamic blacklist/whitelist from either environment variable or file."""
        dynamic_content = os.getenv(env_var, "")
        if dynamic_content:
            return dynamic_content
        with open(file_path, "r") as file:
            return file.read()
