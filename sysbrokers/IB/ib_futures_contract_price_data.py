from syscore.objects import missing_contract
from sysproduction.data.directories import missing_data

from sysbrokers.IB.ib_futures_contracts_data import ibFuturesContractData
from sysbrokers.IB.ib_instruments_data import ibFuturesInstrumentData
from sysbrokers.IB.ib_translate_broker_order_objects import sign_from_BS, ibBrokerOrder
from sysbrokers.IB.ib_connection import connectionIB
from sysbrokers.IB.client.ib_price_client import tickerWithBS, ibPriceClient

from sysdata.futures.futures_per_contract_prices import (
    futuresContractPriceData,
)


from sysexecution.tick_data import tickerObject, dataFrameOfRecentTicks
from sysexecution.orders.contract_orders import contractOrder


from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.contracts import futuresContract, listOfFuturesContracts


from syslogdiag.log import logtoscreen


class ibTickerObject(tickerObject):
    def __init__(self, ticker_with_BS: tickerWithBS, broker_client: ibPriceClient):
        ticker = ticker_with_BS.ticker
        BorS = ticker_with_BS.BorS

        # qty can just be +1 or -1 size of trade doesn't matter to ticker
        qty = sign_from_BS(BorS)
        super().__init__(ticker, qty=qty)
        self._broker_client = broker_client

    def refresh(self):
        self._broker_client.refresh()

    def bid(self):
        return self.ticker.bid

    def ask(self):
        return self.ticker.ask

    def bid_size(self):
        return self.ticker.bidSize

    def ask_size(self):
        return self.ticker.askSize


def from_ib_bid_ask_tick_data_to_dataframe(tick_data) ->dataFrameOfRecentTicks:
    """

    :param tick_data: list of HistoricalTickBidAsk()
    :return: pd.DataFrame,['priceBid', 'priceAsk', 'sizeAsk', 'sizeBid']
    """
    time_index = [tick_item.time for tick_item in tick_data]
    fields = ["priceBid", "priceAsk", "sizeAsk", "sizeBid"]

    value_dict = {}
    for field_name in fields:
        field_values = [getattr(tick_item, field_name)
                        for tick_item in tick_data]
        value_dict[field_name] = field_values

    output = dataFrameOfRecentTicks(value_dict, time_index)

    return output



class ibFuturesContractPriceData(futuresContractPriceData):
    """
    Extends the baseData object to a data source that reads in and writes prices for specific futures contracts

    This gets HISTORIC data from interactive brokers. It is blocking code
    In a live production system it is suitable for running on a daily basis to get end of day prices

    """

    def __init__(self, ibconnection: connectionIB, log=logtoscreen(
            "ibFuturesContractPriceData")):
        self._ibconnection = ibconnection
        super().__init__(log=log)

    def __repr__(self):
        return "IB Futures per contract price data %s" % str(self.ib_client)

    @property
    def ibconnection(self) -> connectionIB:
        return  self._ibconnection

    @property
    def ib_client(self) -> ibPriceClient:
        client = getattr(self, "_ib_client", None)
        if client is None:
             client = self._ib_client = ibPriceClient(ibconnection=self.ibconnection,
                                        log = self.log)

        return client

    @property
    def log(self):
        return self._log


    @property
    def futures_contract_data(self) -> ibFuturesContractData:
        return ibFuturesContractData(self.ibconnection, log = self.log)

    @property
    def futures_instrument_data(self) -> ibFuturesInstrumentData:
        return ibFuturesInstrumentData(self.ibconnection, log = self.log)

    def has_data_for_contract(self, futures_contract: futuresContract) -> bool:
        """
        Does IB have data for a given contract?

        Overriden because we will have a problem matching expiry dates to nominal yyyymm dates
        :param contract_object:
        :return: bool
        """
        futures_contract_with_IB_data = self.futures_contract_data.get_contract_object_with_IB_data(
            futures_contract)
        if futures_contract_with_IB_data is missing_contract:
            return False
        else:
            return True

    def get_list_of_instrument_codes_with_price_data(self) -> list:
        # return list of instruments for which pricing is configured
        list_of_instruments = self.futures_instrument_data.get_list_of_instruments()

        return list_of_instruments

    def contracts_with_price_data_for_instrument_code(self, instrument_code: str) -> listOfFuturesContracts:
        futures_instrument_with_ib_data = self.futures_instrument_data.get_futures_instrument_object_with_IB_data(instrument_code)
        list_of_date_str = self.ib_client.broker_get_futures_contract_list(futures_instrument_with_ib_data)

        list_of_contracts = [futuresContract(instrument_code, date_str) for date_str in list_of_date_str]

        list_of_contracts = listOfFuturesContracts(list_of_contracts)

        return list_of_contracts

    def get_contracts_with_price_data(self):
        raise NotImplementedError(
            "Do not use get_contracts_with_price_data with IB")


    def _get_prices_for_contract_object_no_checking(self, contract_object: futuresContract) -> futuresContractPrices:
        return self._get_prices_at_frequency_for_contract_object_no_checking(
            contract_object, freq="D"
        )

    def _get_prices_at_frequency_for_contract_object_no_checking(self, contract_object: futuresContract,
                                                                 freq: str
                                                    ) -> futuresContractPrices:

        """
        Get historical prices at a particular frequency

        We override this method, rather than _get_prices_at_frequency_for_contract_object_no_checking
        Because the list of dates returned by contracts_with_price_data is likely to not match (expiries)

        :param contract_object:  futuresContract
        :param freq: str; one of D, H, 15M, 5M, M, 10S, S
        :return: data
        """
        new_log = contract_object.log(self.log)

        contract_object_with_ib_broker_config = (
            self.futures_contract_data.get_contract_object_with_IB_data(
                contract_object
            )
        )
        if contract_object_with_ib_broker_config is missing_contract:
            new_log.warn("Can't get data for %s" % str(contract_object))
            return futuresContractPrices.create_empty()

        price_data = self.ib_client.broker_get_historical_futures_data_for_contract(
            contract_object_with_ib_broker_config, bar_freq=freq)

        if price_data is missing_data:
            new_log.warn(
                "Something went wrong getting IB price data for %s" %
                str(contract_object))
            price_data = futuresContractPrices.create_empty()

        elif len(price_data) == 0:
            new_log.warn(
                "No IB price data found for %s" %
                str(contract_object))
            price_data = futuresContractPrices.create_empty()
        else:
            price_data = futuresContractPrices(price_data)

        ## It's important that the data is in local time zone so that this works
        price_data = price_data.remove_future_data()

        ## Some contract data is marked to model, don't want this
        price_data = price_data.remove_zero_volumes()

        return price_data


    def get_ticker_object_for_order(self, order: contractOrder) -> tickerObject:
        contract_object = order.futures_contract
        trade_list_for_multiple_legs = order.trade

        new_log = order.log_with_attributes(self.log)

        contract_object_with_ib_data = (
            self.futures_contract_data.get_contract_object_with_IB_data(contract_object)
        )
        if contract_object_with_ib_data is missing_contract:
            new_log.warn("Can't get data for %s" % str(contract_object))
            return futuresContractPrices.create_empty()

        ticker_with_bs = self.ib_client.get_ticker_object(
            contract_object_with_ib_data,
            trade_list_for_multiple_legs=trade_list_for_multiple_legs,
        )

        ticker_object = ibTickerObject(ticker_with_bs, self.ib_client)

        return ticker_object


    def cancel_market_data_for_order(self, order: ibBrokerOrder):
        contract_object = order.futures_contract
        trade_list_for_multiple_legs = order.trade

        new_log = order.log_with_attributes(self.log)

        contract_object_with_ib_data = (
            self.futures_contract_data.get_contract_object_with_IB_data(
                contract_object
            )
        )
        if contract_object_with_ib_data is missing_contract:
            new_log.warn("Can't get data for %s" % str(contract_object))
            return futuresContractPrices.create_empty()

        self.ib_client.cancel_market_data_for_contract_object(
            contract_object_with_ib_data,
            trade_list_for_multiple_legs=trade_list_for_multiple_legs,
        )

    def get_recent_bid_ask_tick_data_for_contract_object(self, contract_object: futuresContract) ->dataFrameOfRecentTicks:
        """
        Get last few price ticks

        :param contract_object: futuresContract
        :return:
        """
        new_log = contract_object.log(self.log)

        contract_object_with_ib_data = (
            self.futures_contract_data.get_contract_object_with_IB_data(
                contract_object
            )
        )
        if contract_object_with_ib_data is missing_contract:
            new_log.warn("Can't get data for %s" % str(contract_object))
            return futuresContractPrices.create_empty()

        tick_data = self.ib_client.ib_get_recent_bid_ask_tick_data(
            contract_object_with_ib_data)

        if tick_data is missing_contract:
            return missing_data

        tick_data_as_df = from_ib_bid_ask_tick_data_to_dataframe(tick_data)

        return tick_data_as_df



    def _write_prices_for_contract_object_no_checking(self, *args, **kwargs):
        raise NotImplementedError("IB is a read only source of prices")

    def delete_prices_for_contract_object(self, *args, **kwargs):
        raise NotImplementedError("IB is a read only source of prices")

    def _delete_prices_for_contract_object_with_no_checks_be_careful(
        self, futures_contract_object: futuresContract
    ):
        raise NotImplementedError("IB is a read only source of prices")
