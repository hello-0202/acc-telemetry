"""
Test ACC Memory Map Control
"""

from __future__ import annotations

import logging
import sys

sys.path.append(__file__.split("pyACCSharedMemory")[0])
from pyACCSharedMemory import acc_data, acc_mmap


def test_api():
    # Add logger
    logger = logging.getLogger(__name__)
    test_handler = logging.StreamHandler()
    logger.setLevel(logging.INFO)
    logger.addHandler(test_handler)
    logger.info(__doc__)

    # Test run
    SEPARATOR = "=" * 50
    print("Test API - Direct Access")
    info = acc_mmap.MMapControl(acc_data.ACCConstants.MM_STATIC_FILE_NAME, acc_data.ACCStatic)
    info.create(1)
    info.update()

    print(SEPARATOR)
    print("Test API - Close")
    info.close()

    print(SEPARATOR)
    print("Test API - Copy Access")
    info.create(0)
    info.update()

    print(SEPARATOR)
    print("Test API - Read")
    version = info.data.acVersion
    track = info.data.trackName
    vehicle = info.data.carModel
    total = info.data.numberOfCars
    print(f"version: {version if version else 'not running'}")
    print(f"track name: {track if version else 'not running'}")
    print(f"vehicle name: {vehicle if version else 'not running'}")
    print(f"total cars: {total if version else 'not running'}")

    print(SEPARATOR)
    info.close()


if __name__ == "__main__":
    test_api()
