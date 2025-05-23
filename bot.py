import logging
from typing import Dict, List
from datetime import datetime, timedelta, date
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler, 
    CallbackContext, ConversationHandler, MessageHandler, Filters
)
from rzd_api import RzdAPI  # Импорт нашего нового класса

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
SELECT_STATION_FROM, SELECT_STATION_TO, SELECT_DATE, CONFIRM_SEARCH = range(4)

class RzdTicketBot:
    def __init__(self, token: str):
        self.updater = Updater(token)
        self.dispatcher = self.updater.dispatcher
        self.user_searches: Dict[int, Dict] = {}
        self.active_subscriptions: Dict[int, List[Dict]] = {}
        self.rzd_api = RzdAPI()  # Создаем экземпляр нашего API
        
        self._register_handlers()
        
    def _register_handlers(self):
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('search', self.search_start)],
            states={
                SELECT_STATION_FROM: [
                    MessageHandler(Filters.text & ~Filters.command, self.select_station_from)
                ],
                SELECT_STATION_TO: [
                    MessageHandler(Filters.text & ~Filters.command, self.select_station_to)
                ],
                SELECT_DATE: [
                    MessageHandler(Filters.text & ~Filters.command, self.select_date)
                ],
                CONFIRM_SEARCH: [
                    CallbackQueryHandler(self.confirm_search, pattern='^(confirm|cancel)_search$')
                ],
            },
            fallbacks=[CommandHandler('cancel', self.cancel)],
        )
        
        self.dispatcher.add_handler(CommandHandler("start", self.start))
        self.dispatcher.add_handler(CommandHandler("help", self.help))
        self.dispatcher.add_handler(CommandHandler("subscriptions", self.show_subscriptions))
        self.dispatcher.add_handler(CallbackQueryHandler(self.subscribe, pattern='^subscribe_'))
        self.dispatcher.add_handler(conv_handler)
        self.dispatcher.add_error_handler(self.error_handler)

    def start(self, update: Update, context: CallbackContext) -> None:
        """Обработка команды /start"""
        user = update.effective_user
        update.message.reply_text(
            f"Привет, {user.first_name}!\n"
            "Я бот для отслеживания железнодорожных билетов РЖД.\n"
            "Используй /search для поиска билетов или /help для справки."
        )
    
    def help(self, update: Update, context: CallbackContext) -> None:
        """Обработка команды /help"""
        update.message.reply_text(
            "Доступные команды:\n"
            "/start - начать работу с ботом\n"
            "/search - поиск билетов\n"
            "/subscriptions - просмотр активных подписок\n"
            "/help - эта справка\n\n"
            "Бот может уведомлять вас о появлении билетов на выбранные направления."
        )

    def search_start(self, update: Update, context: CallbackContext) -> int:
        """Начало поиска - запрос станции отправления"""
        update.message.reply_text(
            "Введите станцию отправления (например: Москва):"
        )
        return SELECT_STATION_FROM
    
    def show_subscriptions(self, update: Update, context: CallbackContext) -> None:
        """Показ активных подписок пользователя"""
        user_id = update.effective_user.id
        if user_id not in self.active_subscriptions or not self.active_subscriptions[user_id]:
            update.message.reply_text("У вас нет активных подписок.")
            return
        
        response = "Ваши активные подписки:\n\n"
        for sub in self.active_subscriptions[user_id]:
            response += (
                f"Маршрут: {sub['from']['name']} → {sub['to']['name']}\n"
                f"Дата: {sub['date']}\n"
                f"Последняя проверка: {sub['last_check'].strftime('%d.%m.%Y %H:%M')}\n\n"
            )
        
        update.message.reply_text(response)

    def check_tickets_periodically(self, context: CallbackContext) -> None:
        """Периодическая проверка билетов для активных подписок"""
        for user_id, subscriptions in self.active_subscriptions.items():
            for sub in subscriptions:
                try:
                    # Получаем актуальные билеты через наш экземпляр API
                    tickets = self.rzd_api.get_tickets(
                        from_code=sub['from']['code'],
                        to_code=sub['to']['code'],
                        date=datetime.strptime(sub['date'], "%d.%m.%Y")
                    )

                    search_date = datetime.strptime(sub['date'], "%d.%m.%Y").date()
                    if search_date < datetime.now().date():
                        continue

                    if tickets is None:
                        continue
                    
                    # Сравниваем с предыдущими результатами
                    new_tickets = self._find_new_tickets(sub['tickets'], tickets)
                    
                    if new_tickets:
                        # Отправляем уведомление о новых билетах
                        message = "🚀 Появились новые билеты:\n\n"
                        for ticket in new_tickets:
                            message += (
                                f"🚂 Поезд: {ticket.get('number', 'N/A')}\n"
                                f"🕒 Отправление: {ticket.get('departure', 'N/A')}\n"
                                f"🕓 Прибытие: {ticket.get('arrival', 'N/A')}\n"
                                f"💰 Цена: {self._format_price(ticket.get('seats', []))}\n"
                                f"💺 Места: {self._format_seats(ticket.get('seats', []))}\n\n"
                            )
                        
                        context.bot.send_message(
                            chat_id=user_id,
                            text=message
                        )
                    
                    # Обновляем данные подписки
                    sub['tickets'] = tickets
                    sub['last_check'] = datetime.now(pytz.utc)
                    
                except Exception as e:
                    logger.error(f"Ошибка при проверке билетов для пользователя {user_id}: {e}")

    
    def _find_new_tickets(self, old_tickets: List[Dict], new_tickets: List[Dict]) -> List[Dict]:
        """Поиск новых билетов по сравнению с предыдущей проверкой"""
        old_numbers = {t.get('number') for t in old_tickets if t.get('number')}
        return [t for t in new_tickets if t.get('number') not in old_numbers]
    
    def select_date(self, update: Update, context: CallbackContext) -> int:
        """Обработка выбора даты"""
        date_str = update.message.text
        try:
            date = datetime.strptime(date_str, "%d.%m.%Y").date()
            if date < datetime.now().date():
                raise ValueError("Дата в прошлом")
        except ValueError as e:
            update.message.reply_text("Некорректная дата. Введите дату в формате ДД.ММ.ГГГГ:")
            return SELECT_DATE
        
        context.user_data['date'] = date_str
        
        # Формируем подтверждение поиска
        station_from = context.user_data['station_from']
        station_to = context.user_data['station_to']
        
        keyboard = [
            [
                InlineKeyboardButton("Подтвердить", callback_data="confirm_search"),
                InlineKeyboardButton("Отмена", callback_data="cancel_search"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            f"Параметры поиска:\n"
            f"Отправление: {station_from['name']}\n"
            f"Прибытие: {station_to['name']}\n"
            f"Дата: {date_str}\n\n"
            f"Подтвердить поиск?",
            reply_markup=reply_markup
        )
        return CONFIRM_SEARCH

    def select_station_from(self, update: Update, context: CallbackContext) -> int:
        """Обработка выбора станции отправления с использованием нашего API"""
        query = update.message.text
        stations = self.rzd_api.station_by_name(query)
        
        if not stations:
            update.message.reply_text("🚂 Станции не найдены. Попробуйте еще раз:")
            return SELECT_STATION_FROM
        
        context.user_data['station_from'] = {
            'code': stations[0]['code'],
            'name': stations[0]['name']
        }
        
        update.message.reply_text(
            f"📍 Выбрана станция: {stations[0]['name']}\n"
            "Теперь введите станцию назначения:"
        )
        return SELECT_STATION_TO

    def select_station_to(self, update: Update, context: CallbackContext) -> int:
        """Обработка выбора станции назначения"""
        query = update.message.text
        stations = self.rzd_api.station_by_name(query)
        
        if not stations:
            update.message.reply_text("🚂 Станции не найдены. Попробуйте еще раз:")
            return SELECT_STATION_TO
        
        context.user_data['station_to'] = {
            'code': stations[0]['code'],
            'name': stations[0]['name']
        }
        
        update.message.reply_text(
            f"📍 Выбрана станция: {stations[0]['name']}\n"
            "Теперь введите дату поездки в формате ДД.ММ.ГГГГ:"
        )
        return SELECT_DATE

    def confirm_search(self, update: Update, context: CallbackContext) -> int:
        """Подтверждение поиска с использованием нашего API"""
        query = update.callback_query
        query.answer()
        
        if query.data == 'cancel_search':
            query.edit_message_text("❌ Поиск отменен")
            return ConversationHandler.END
        
        user_id = update.effective_user.id
        search_params = context.user_data
        
        try:
            # Используем наш метод get_tickets вместо tickets
            tickets = self.rzd_api.get_tickets(
                from_code=search_params['station_from']['code'],
                to_code=search_params['station_to']['code'],
                date=datetime.strptime(search_params['date'], "%d.%m.%Y")
            )
        except Exception as e:
            logger.error(f"Ошибка при поиске билетов: {e}")
            query.edit_message_text("⚠️ Произошла ошибка при поиске билетов. Попробуйте позже.")
            return ConversationHandler.END
        
        if not tickets:
            query.edit_message_text("😞 Билеты не найдены.")
            return ConversationHandler.END
        
        # Сохраняем поиск
        if user_id not in self.user_searches:
            self.user_searches[user_id] = []
        
        search_data = {
            'from': search_params['station_from'],
            'to': search_params['station_to'],
            'date': search_params['date'],
            'last_check': datetime.now(pytz.utc),
            'tickets': tickets
        }
        
        self.user_searches[user_id].append(search_data)
        
        # Формируем ответ
        response = "🎫 Найденные билеты:\n\n"
        for ticket in tickets[:5]:  # Показываем первые 5 вариантов
            response += (
                f"🚂 Поезд: {ticket.get('number', 'N/A')}\n"
                f"🕒 Отправление: {ticket.get('departure', 'N/A')}\n"
                f"🕓 Прибытие: {ticket.get('arrival', 'N/A')}\n"
                f"💰 Примерная цена: {self._format_price(ticket.get('seats', []))}\n"
                f"💺 Места: {self._format_seats(ticket.get('seats', []))}\n\n"
            )
        
        query.edit_message_text(response)
        
        # Предлагаем подписаться
        keyboard = [[
            InlineKeyboardButton(
                "🔔 Подписаться на обновления", 
                callback_data=f"subscribe_{len(self.user_searches[user_id])-1}")
        ]]
        update.message.reply_text(
            "Хотите получать уведомления о новых билетах?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return ConversationHandler.END

    def _format_price(self, seats: List[Dict]) -> str:
        """Форматирование информации о ценах"""
        if not seats:
            return "не указана"
        
        prices = [float(s.get('price')) for s in seats if s.get('price')]
        if not prices:
            return "не указана"
        
        min_price = min(prices)
        return f"от {min_price} руб."

    def _format_seats(self, seats: List[Dict]) -> str:
        """Форматирование информации о местах"""
        if not seats:
            return "нет данных"
        
        free_seats = sum(int(s.get('free', 0)) for s in seats)
        return f"{free_seats} свободных" if free_seats > 0 else "нет мест"
    
    def cancel(self, update: Update, context: CallbackContext) -> int:
        """Отмена текущего действия"""
        update.message.reply_text('Действие отменено.')
        return ConversationHandler.END
    
    def error_handler(self, update: Update, context: CallbackContext) -> None:
        """Обработка ошибок"""
        logger.error(msg="Исключение при обработке обновления:", exc_info=context.error)
        
        if update and update.effective_message:
            update.effective_message.reply_text(
                "Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь в поддержку."
            )

    def subscribe(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        query.answer()
        
        user_id = update.effective_user.id
        search_idx = int(query.data.split('_')[1])
        
        if user_id not in self.active_subscriptions:
            self.active_subscriptions[user_id] = []
        
        search_data = self.user_searches[user_id][search_idx]
        self.active_subscriptions[user_id].append(search_data)
        
        query.edit_message_text("✅ Вы подписались на обновления по этому маршруту!")

    def run(self):
        """Запуск бота с периодической проверкой"""
        job_queue = self.updater.job_queue
        job_queue.run_repeating(
            self.check_tickets_periodically, 
            interval=1800,  # 30 минут
            first=10
        )
        self.updater.start_polling()
        self.updater.idle()


if __name__ == '__main__':
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    
    if not BOT_TOKEN:
        raise ValueError("Не задан BOT_TOKEN в .env файле")
    
    bot = RzdTicketBot(BOT_TOKEN)
    bot.run()