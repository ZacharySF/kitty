"""The application form filler (Stage 7, pulled forward): fills, never submits.

filler.py drives the browser (WebDriver BiDi over a WebSocket); urls.py only knows
about URLs and is cheap to import. This package imports neither itself.
"""
