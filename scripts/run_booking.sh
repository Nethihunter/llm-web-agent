#!/bin/bash
# Demo 2: Booking.com search (read-only, no reservation)
# Uses OpenAI by default — Booking may block DeepSeek's IP ranges.

python3 run.py --task booking_search --yes --provider openai
