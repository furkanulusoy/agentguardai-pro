import unittest
from unittest import TestCase
from unittest.mock import MagicMock

from github.GithubException import BadCredentialsException

from connectors.base import ConnectorAuthenticationError
from connectors.github.connector import GitHubConnector


class TestGitHubConnector(TestCase):
    def test_list_repos_classifies_rejected_token_as_authentication_failure(self):
        connector = GitHubConnector()
        connector._client = MagicMock()
        connector._client.get_user.return_value.get_repos.side_effect = (
            BadCredentialsException(401, {"message": "Bad credentials"}, {})
        )

        with self.assertRaisesRegex(
            ConnectorAuthenticationError, "GitHub credential was rejected"
        ):
            connector.list_repos()


if __name__ == "__main__":
    unittest.main()
