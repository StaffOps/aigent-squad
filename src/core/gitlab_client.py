import os
import requests
from typing import List, Dict, Optional
from src.core.logger import logger

class GitLabClient:
    """GitLab API client for read-only access to Company repositories"""
    
    def __init__(self):
        self.token = os.getenv("GITLAB_TOKEN")
        self.base_url = "https://gitlab.com/api/v4"
        self.headers = {"PRIVATE-TOKEN": self.token} if self.token else {}
        
        # Company - FULL ACCESS to entire tree
        self.company_group = "Company"
        
        # Priority areas (most important, but not exclusive)
        self.devops_group = "your-organization/devops"
        self.infra_group = "your-organization/infrastructure"
        self.docs_project = "your-organization/devops/DOCUMENTATION/devops-docs"
        
        # Documentation URLs
        self.docs_url_current = "https://docs.company.internal/"
        self.docs_url_new = "https://docs.company.com/"
    
    def search_in_company(self, query: str, scope: str = "blobs", limit: int = 20) -> List[Dict]:
        """Search across ENTIRE Company organization"""
        try:
            response = requests.get(
                f"{self.base_url}/groups/{self._encode_project(self.company_group)}/search",
                headers=self.headers,
                params={"scope": scope, "search": query, "per_page": limit},
                timeout=10
            )
            response.raise_for_status()
            
            results = response.json()
            logger.info("GitLab Company search", extra={
                "query": query,
                "scope": scope,
                "results_count": len(results)
            })
            
            return results
            
        except Exception as e:
            logger.error("Error searching Company", extra={"error": str(e)})
            return []
    
    def search_documentation(self, query: str, limit: int = 10) -> List[Dict]:
        """Search in devops-docs repository (priority)"""
        try:
            response = requests.get(
                f"{self.base_url}/projects/{self._encode_project(self.docs_project)}/search",
                headers=self.headers,
                params={"scope": "blobs", "search": query, "per_page": limit},
                timeout=10
            )
            response.raise_for_status()
            
            results = response.json()
            logger.info("GitLab documentation search", extra={
                "query": query,
                "results_count": len(results)
            })
            
            return results
            
        except Exception as e:
            logger.error("Error searching GitLab docs", extra={"error": str(e)})
            return []
    
    def get_file_content(self, project: str, file_path: str, ref: str = "main") -> Optional[str]:
        """Get file content from ANY Company repository"""
        try:
            response = requests.get(
                f"{self.base_url}/projects/{self._encode_project(project)}/repository/files/{self._encode_path(file_path)}/raw",
                headers=self.headers,
                params={"ref": ref},
                timeout=10
            )
            response.raise_for_status()
            return response.text
            
        except Exception as e:
            logger.error("Error fetching file from GitLab", extra={
                "project": project,
                "file_path": file_path,
                "error": str(e)
            })
            return None
    
    def list_all_projects(self, include_subgroups: bool = True) -> List[Dict]:
        """List ALL projects in Company organization"""
        try:
            response = requests.get(
                f"{self.base_url}/groups/{self._encode_project(self.company_group)}/projects",
                headers=self.headers,
                params={"per_page": 100, "include_subgroups": include_subgroups},
                timeout=10
            )
            response.raise_for_status()
            
            projects = response.json()
            logger.info("GitLab projects listed", extra={
                "group": self.company_group,
                "count": len(projects)
            })
            
            return projects
            
        except Exception as e:
            logger.error("Error listing GitLab projects", extra={"error": str(e)})
            return []
    
    def list_projects(self, group: str) -> List[Dict]:
        """List projects in specific group"""
        try:
            response = requests.get(
                f"{self.base_url}/groups/{self._encode_project(group)}/projects",
                headers=self.headers,
                params={"per_page": 100, "include_subgroups": True},
                timeout=10
            )
            response.raise_for_status()
            
            projects = response.json()
            logger.info("GitLab projects listed", extra={
                "group": group,
                "count": len(projects)
            })
            
            return projects
            
        except Exception as e:
            logger.error("Error listing GitLab projects", extra={
                "group": group,
                "error": str(e)
            })
            return []
    
    def get_repository_tree(self, project: str, path: str = "", ref: str = "main") -> List[Dict]:
        """Get repository tree from ANY Company project"""
        try:
            params = {"ref": ref, "per_page": 100, "recursive": True}
            if path:
                params["path"] = path
            
            response = requests.get(
                f"{self.base_url}/projects/{self._encode_project(project)}/repository/tree",
                headers=self.headers,
                params=params,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error("Error fetching repository tree", extra={
                "project": project,
                "error": str(e)
            })
            return []
    
    def search_code(self, query: str, group: str = None) -> List[Dict]:
        """Search code - defaults to entire Company if no group specified"""
        try:
            if group:
                url = f"{self.base_url}/groups/{self._encode_project(group)}/search"
            else:
                # Search entire Company by default
                url = f"{self.base_url}/groups/{self._encode_project(self.company_group)}/search"
            
            response = requests.get(
                url,
                headers=self.headers,
                params={"scope": "blobs", "search": query, "per_page": 20},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error("Error searching code in GitLab", extra={"error": str(e)})
            return []
    
    def get_docs_url(self) -> str:
        """Get current documentation URL (checks if migration happened)"""
        # TODO: Add logic to detect when migration to new URL happened
        # For now, return current URL
        return self.docs_url_current
    
    def _encode_project(self, project: str) -> str:
        """URL encode project path"""
        return requests.utils.quote(project, safe='')
    
    def _encode_path(self, path: str) -> str:
        """URL encode file path"""
        return requests.utils.quote(path, safe='')


# Singleton
gitlab_client = GitLabClient()
