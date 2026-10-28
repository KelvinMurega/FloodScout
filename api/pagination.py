"""Paging for list endpoints, on by default (settings.REST_FRAMEWORK).

A list nobody bounds is a way to make the server read a whole table per
request. New endpoints get pages unless they say otherwise. The endpoints
the dashboard reads as plain lists opt out with `pagination_class = None`,
and each says why its list stays small.
"""

from rest_framework.pagination import PageNumberPagination


class Pages(PageNumberPagination):
    """?page=<n>, ?page_size=<up to 500>; {count, next, previous, results}."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500
