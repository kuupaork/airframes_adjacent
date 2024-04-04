# This is simply a mapping of all ground stations and their assigned frequencies. It does not mean any specific one
# will be in use at any given time. It's provided as a fallback in case airframes is not available and 
# squitter-compiled frequency lists are (not) yet compiled.
import datetime

fake_now = - 3600

ALL_FREQUENCIES = {
    "ground_stations": [
        {
            "id": 1,
            "name": "San Francisco, California",
            "frequencies": {
                "active": [21934, 17919, 12276, 11327, 10081, 8927, 6559, 5508]
            },
            "last_updated": fake_now
        },
        {
            "id": 2,
            "name": "Molokai, Hawaii",
            "frequencies": {
                "active": [21937, 17919, 13324, 13312, 13276, 11348, 11312, 10027, 8936, 8912, 6565, 5514]
            },
            "last_updated": fake_now
        },
        {
            "id": 3,
            "name": "Reykjavik, Iceland",
            "frequencies": {
                "active": [17985, 15025, 11184, 8977, 6712, 5720, 3900]
            },
            "last_updated": fake_now
        },
        {
            "id": 4,
            "name": "Riverhead, New York",
            "frequencies": {
                "active": [21931, 17919, 13276, 11387, 8912, 6661, 5652]
            },
            "last_updated": fake_now
        },
        {
            "id": 5,
            "name": "Auckland, New Zealand",
            "frequencies": {
                "active": [17916, 13351, 10084, 8921, 6535, 5583]
            },
            "last_updated": fake_now
        },
        {
            "id": 6,
            "name": "Hat Yai, Thailand",
            "frequencies": {
                "active": [21949, 17928, 13270, 10066, 8825, 6535, 5655]
            },
            "last_updated": fake_now
        },
        {
            "id": 7,
            "name": "Shannon, Ireland",
            "frequencies": {
                "active": [11384, 10081, 8942, 8843, 6532, 5547, 3455, 2998]
            },
            "last_updated": fake_now
        },
        {
            "id": 8,
            "name": "Johannesburg, South Africa",
            "frequencies": {
                "active": [21949, 17922, 13321, 11321, 8834, 5529, 4681, 3016]
            },
            "last_updated": fake_now
        },
        {
            "id": 9,
            "name": "Barrow, Alaska",
            "frequencies": {
                "active": [21937, 21928, 17934, 17919, 11354, 10093, 10027, 8936, 8927, 6646, 5544, 5538, 5529, 4687, 4654, 3497, 3007, 2992, 2944]
            },
            "last_updated": fake_now
        },
        {
            "id": 10,
            "name": "Muan, South Korea",
            "frequencies": {
                "active": [21931, 17958, 13342, 10060, 8939, 6619, 5502, 2941]
            },
            "last_updated": fake_now
        },
        {
            "id": 11,
            "name": "Albrook, Panama",
            "frequencies": {
                "active": [17901, 13264, 10063, 8894, 6589, 5589]
            },
            "last_updated": fake_now
        },
        {
            "id": 12,
            "name": "No longer in service",
            "frequencies": {
                "active": []
            },
            "last_updated": fake_now
        },
        {
            "id": 13,
            "name": "Santa Cruz, Bolivia",
            "frequencies": {
                "active": [21997, 17916, 13315, 11318, 8957, 6628, 4660]
            },
            "last_updated": fake_now
        },
        {
            "id": 14,
            "name": "Krasnoyarsk, Russia",
            "frequencies": {
                "active": [21990, 17912, 13321, 10087, 8886, 6596, 5622]
            },
            "last_updated": fake_now
        },
        {
            "id": 15,
            "name": "Al Muharraq, Bahrain",
            "frequencies": {
                "active": [21982, 17967, 13354, 10075, 8885, 5544]
            },
            "last_updated": fake_now
        },
        {
            "id": 16,
            "name": "Agana, Guam",
            "frequencies": {
                "active": [21928, 17919, 13312, 11306, 8927, 6652, 5451]
            },
            "last_updated": fake_now
        },
        {
            "id": 17,
            "name": "Canarias, Spain",
            "frequencies": {
                "active": [21955, 17928, 13303, 11348, 8948, 6529]
            },
            "last_updated": fake_now
        }
    ]
}
