"""Test response grounding guardrails.

This script tests that the chatbot rejects responses that use training data
instead of provided search results.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.chatbot import _validate_response_grounding


def test_validation():
    """Test various response scenarios."""

    print("=" * 70)
    print("RESPONSE GROUNDING VALIDATION TESTS")
    print("=" * 70)

    # Test 1: Empty chunks with short response (should pass)
    print("\n1. Empty chunks + short conversational response")
    response = "Hello! How can I help you today?"
    chunks = []
    is_valid, reason = _validate_response_grounding(response, chunks)
    print(f"   Response: '{response}'")
    print(f"   Chunks: {len(chunks)}")
    print(f"   ✓ PASS" if is_valid else f"   ✗ FAIL: {reason}")
    assert is_valid, "Short conversational responses should be allowed"

    # Test 2: Empty chunks with "no info" response (should pass)
    print("\n2. Empty chunks + 'no info' response")
    response = "I don't have any information about that in our archives. Try searching for something else."
    chunks = []
    is_valid, reason = _validate_response_grounding(response, chunks)
    print(f"   Response: '{response}'")
    print(f"   Chunks: {len(chunks)}")
    print(f"   ✓ PASS" if is_valid else f"   ✗ FAIL: {reason}")
    assert is_valid, "'No info' responses should be allowed with empty chunks"

    # Test 3: Empty chunks with long factual response (should FAIL)
    print("\n3. Empty chunks + long factual response (TRAINING DATA LEAKAGE)")
    response = """Photosynthesis is the process by which plants convert light energy into chemical energy.
    It occurs in the chloroplasts of plant cells and involves the conversion of carbon dioxide and water
    into glucose and oxygen. This process is essential for life on Earth as it produces oxygen and serves
    as the base of most food chains. The light-dependent reactions occur in the thylakoid membranes..."""
    chunks = []
    is_valid, reason = _validate_response_grounding(response, chunks)
    print(f"   Response: '{response[:100]}...'")
    print(f"   Chunks: {len(chunks)}")
    print(f"   ✗ FAIL: {reason}" if not is_valid else f"   ✓ PASS (unexpected)")
    assert not is_valid, "Long factual responses without chunks should be rejected"
    assert reason == "long_response_no_chunks"

    # Test 4: Training data indicator phrases (should FAIL)
    print("\n4. Response with training data indicator phrase")
    response = "As of my knowledge cutoff, the capital of France is Paris. This has been the case since..."
    chunks = [{"text": "dummy", "metadata": {}}]
    is_valid, reason = _validate_response_grounding(response, chunks)
    print(f"   Response: '{response}'")
    print(f"   Chunks: {len(chunks)}")
    print(f"   ✗ FAIL: {reason}" if not is_valid else f"   ✓ PASS (unexpected)")
    assert not is_valid, "Responses mentioning 'knowledge cutoff' should be rejected"
    assert "training_data_phrase" in reason

    # Test 5: Valid response with chunks (should pass)
    print("\n5. Valid response with search results")
    response = """According to the article, the city council approved the new park development plan.
    The project will include playground equipment and walking trails. Construction is expected to begin
    next month."""
    chunks = [
        {"text": "City council approves park development...", "metadata": {"title": "Park Approved"}},
        {"text": "New playground and trails planned...", "metadata": {"title": "Park Details"}},
    ]
    is_valid, reason = _validate_response_grounding(response, chunks)
    print(f"   Response: '{response[:80]}...'")
    print(f"   Chunks: {len(chunks)}")
    print(f"   ✓ PASS" if is_valid else f"   ✗ FAIL: {reason}")
    assert is_valid, "Valid responses with chunks should pass"

    # Test 6: AI self-reference phrase (should FAIL)
    print("\n6. Response with AI self-reference")
    response = "I'm an AI assistant and I don't have access to that information. However, generally speaking..."
    chunks = []
    is_valid, reason = _validate_response_grounding(response, chunks)
    print(f"   Response: '{response}'")
    print(f"   Chunks: {len(chunks)}")
    print(f"   ✗ FAIL: {reason}" if not is_valid else f"   ✓ PASS (unexpected)")
    assert not is_valid, "AI self-references should be rejected"

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED ✓")
    print("=" * 70)
    print("\nGuardrails are working correctly!")
    print("\nWhat this prevents:")
    print("  ✗ Answering from training data when no search results")
    print("  ✗ Using general knowledge instead of database content")
    print("  ✗ AI self-references and disclaimers")
    print("\nWhat this allows:")
    print("  ✓ Short conversational responses (greetings, thanks)")
    print("  ✓ 'No information found' responses")
    print("  ✓ Responses properly grounded in search results")


if __name__ == "__main__":
    test_validation()
