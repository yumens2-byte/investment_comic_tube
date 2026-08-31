from src.publisher import upload_to_youtube

def run_e2e_test():
    print("=== EDT Universe Final E2E Upload Test ===")
    metadata = {"episode": 103, "villain": "Debt Titan", "theme": "긴축의 심화와 방어선 사수"}
    upload_to_youtube("test_output_short.mp4", metadata)
    print("=== Test Completed Successfully ===")

if __name__ == "__main__":
    run_e2e_test()
