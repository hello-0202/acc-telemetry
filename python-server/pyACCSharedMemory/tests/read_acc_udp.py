"""
Test & read data from ACC's Broadcasting Network Protocol (UDP)
"""

import logging
import sys
from time import monotonic, sleep

sys.path.append(__file__.split("pyACCSharedMemory")[0])
from pyACCSharedMemory import acc_data, acc_enum, acc_udp


def test_udp():
    # Add logger
    logger = logging.getLogger(__name__)
    test_handler = logging.StreamHandler()
    logger.setLevel(logging.INFO)
    logger.addHandler(test_handler)
    logger.info(__doc__)

    udp_host = "127.0.0.1"
    udp_port = 9000
    update_interval = 0.25
    client_name = "tester"

    dataset = acc_udp.UDPBroadcastOutput()
    print("Host/Port:", udp_host, udp_port)
    print("Update interval:", update_interval)
    print("Output data structure size:", acc_udp.UDPBroadcastOutput.size())

    # Set connection message
    connection_message = acc_udp.set_register_message(
        display_name=client_name,
        connection_password="",
        realtime_update_interval=update_interval * 1000,
        command_password="read-only",
    )

    # Clean any obsolete clients first
    if acc_udp.clean_obsolete_client(
        "127.0.0.1", 9000, client_name, acc_data.ACCConstants.LOG_PATH
    ):
        sleep(1.0)

    with acc_udp.acc_udp_connect(
        udp_host="127.0.0.1",
        udp_port=9000,
        udp_output=dataset,
        connection_message=connection_message,
        connection_timeout=5,
        blocking=True,
    ) as sock:
        connection_id = dataset.registration.connectionId
        print("Client ID:", connection_id)
        # Enable entry list sync
        dataset.entryList.syncEntryList = True
        # Request track data, 11=outbound_type.REQUEST_TRACK_DATA
        message = acc_udp.set_message(11, connection_id)
        sock.send(message)
        # Set entry list message, 10=outbound_type.REQUEST_ENTRY_LIST
        sync_entry_message = acc_udp.set_message(acc_udp.OutboundMessageTypes.REQUEST_ENTRY_LIST, connection_id)
        # Start update loop
        last_timestamp = 0.0
        last_car_entry_count = 0
        buffer_size = acc_udp.BroadcastingNetworkProtocol.BUFFER_SIZE

        max_updates = 100
        update_counter = 0
        while max_updates > 0:
            # Sync entry list
            if dataset.entryList.syncEntryList:
                current_timestamp = monotonic()
                if current_timestamp - last_timestamp > 5:
                    last_timestamp = current_timestamp
                    sock.send(sync_entry_message)
                    dataset.entryList.syncEntryList = False
            # Parse response data
            message_type = acc_udp.parse_udp_stream(sock.recv(buffer_size), dataset)
            # Update for number of loops equal to carEntryCount, then wait for update_interval
            car_entry_count = dataset.entryList.carEntryCount
            if last_car_entry_count != car_entry_count:
                last_car_entry_count = car_entry_count
                print("Updated entry list:", car_entry_count)
            # Wait interval after 2=InboundMessageTypes.REALTIME_UPDATE
            if message_type == 2:
                if update_counter > 0:
                    break
                update_counter += 1
                sleep(update_interval)
            max_updates -= 1

        # Print data
        print("track name:", dataset.trackData.trackName.decode())
        print("track length:", dataset.trackData.trackMeters)
        for car_info in dataset.entryList.entryListCars.values():
            car_type = car_info.carModelType
            car_model = acc_enum.ACC_CAR_MODEL_ID(car_type)
            car_place = car_info.position
            first_name = car_info.currentDriverInfo.firstName.decode()
            last_name = car_info.currentDriverInfo.lastName.decode()
            driver_name = f"{first_name} {last_name}"
            print(
                "place:", f"{car_place:02}",
                " driver:", f"{driver_name:<22}",
                " class:", acc_enum.ACC_CAR_CLASS(car_model),
                " model:", acc_enum.ACC_CAR_MODEL(car_model),
                " car id:", car_info.carId,
            )


if __name__ == "__main__":
    test_udp()
    # Manually disconnect client
    #acc_udp.acc_udp_disconnect("127.0.0.1", 9000, list(range(100)))
