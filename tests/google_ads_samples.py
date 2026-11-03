"""Invented Google Ads refusal samples."""
import httpx

from google_project import fixture

SAMPLES = fixture('google_ads', 'refusals')['samples']


def answer_of(sample):
    return httpx.Response(sample['status'], json=sample['body'])
