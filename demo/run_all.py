"""Run all four scenarios back to back."""
from demo import (
    scenario_a_permissions,
    scenario_b_autonomous_action,
    scenario_c_malicious_connector,
    scenario_d_prompt_injection,
)


def main():
    scenario_a_permissions.main()
    scenario_b_autonomous_action.main()
    scenario_c_malicious_connector.main()
    scenario_d_prompt_injection.main()
    print()
    print("=" * 70)
    print("Tüm senaryolar tamamlandı.")
    print("=" * 70)


if __name__ == "__main__":
    main()
