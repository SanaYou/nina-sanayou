#!/bin/bash
cd "$(dirname "$0")"
pip3 install -r requirements.txt -q
uvicorn main:app --reload --port 8000
