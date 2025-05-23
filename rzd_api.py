import requests
from datetime import datetime
import json

class RzdAPI:
    """Python реализация API РЖД"""
    
    BASE_URL = "https://pass.rzd.ru/"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        })
    
    def station_by_name(self, name: str) -> list:
        """Поиск станций по названию"""
        url = f"{self.BASE_URL}suggester"
        params = {
            'stationNamePart': name,
            'lang': 'ru',
            'compactMode': 'y'
        }
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ошибка поиска станции: {e}")
            return []
    
    def get_timetable(self, from_code: str, to_code: str, date: datetime) -> dict:
        """Получение расписания между станциями"""
        url = f"{self.BASE_URL}timetable/public/"
        params = {
            'layer_id': 5827,
            'dir': 0,
            'tfl': 3,
            'checkSeats': 1,
            'code0': from_code,
            'code1': to_code,
            'dt0': date.strftime('%d.%m.%Y')
        }
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Ошибка получения расписания: {e}")
            return {}
    
    def get_tickets(self, from_code: str, to_code: str, date: datetime) -> list:
        """Получение списка билетов"""
        timetable = self.get_timetable(from_code, to_code, date)
        if not timetable:
            return []
        
        # Парсинг результатов (адаптируйте под актуальную структуру ответа)
        trains = timetable.get('tp', [])
        tickets = []
        
        for train in trains:
            tickets.append({
                'number': train.get('number'),
                'departure': f"{train.get('date0')} {train.get('time0')}",
                'arrival': f"{train.get('date1')} {train.get('time1')}",
                'duration': train.get('timeInWay'),
                'seats': self._parse_seats(train)
            })
        
        return tickets
    
    def _parse_seats(self, train_data: dict) -> list:
        """Парсинг информации о местах"""
        seats = []
        cars = train_data.get('cars', [])
        
        for car in cars:
            seats.append({
                'type': car.get('type'),
                'free': car.get('freeSeats'),
                'price': car.get('tariff')
            })
        
        return seats