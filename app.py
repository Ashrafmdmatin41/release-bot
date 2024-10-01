__version__ = "0.1.0"

import asyncio
import threading

import github
import nest_asyncio
import telegram
from flask import Flask
from flask_apscheduler import APScheduler
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from github import Github, Auth

from config import Config

nest_asyncio.apply()

db = SQLAlchemy()
migrate = Migrate()
scheduler = APScheduler()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.logger.setLevel(app.config['LOG_LEVEL'])

    db.init_app(app)
    migrate.init_app(app, db)

    scheduler.init_app(app)

    return app


app = create_app()

import models

if app.config['GITHUB_TOKEN']:
    auth = Auth.Token(app.config['GITHUB_TOKEN'])
else:
    auth = None
github_obj = Github(auth=auth)


@scheduler.task('interval', id='poll_github', hours=1)
def poll_github():
    with (scheduler.app.app_context()):
        for repo_obj in models.Repo.query.all():
            try:
                app.logger.info('Poll GitHub repo %s', repo_obj.full_name)
                repo = github_obj.get_repo(repo_obj.id)
            except github.GithubException as e:
                print("Github Exception in poll_github", e)
                continue

            try:
                release = repo.get_latest_release()
            except github.GithubException as e:
                # Repo has no releases yet
                continue

            if repo_obj.current_release_id != release.id:
                repo_obj.current_release_id = release.id
                repo_obj.current_tag = release.tag_name
                db.session.commit()

                message = (f"<a href='{repo.html_url}'>{repo.full_name}</a>:\n"
                           f"<b>{release.title}</b>"
                           f" <code>{release.tag_name}</code>"
                           f"{" <i>pre-release</i>" if release.prerelease else ""}\n"
                           f"<blockquote>{release.body}</blockquote>"
                           f"<a href='{release.html_url}'>release note...</a>")

                for chat in repo_obj.chats:
                    bot = telegram.Bot(token=app.config['TELEGRAM_BOT_TOKEN'])    # TODO: Use single bot instance
                    try:
                        asyncio.run(bot.send_message(chat_id=chat.id,
                                                     text=message,
                                                     parse_mode='HTML',
                                                     disable_web_page_preview=True))
                    except telegram.error.Forbidden as e:
                        app.logger.info('Bot was blocked by the user')
                        # TODO: Delete empty repos
                        db.session.delete(chat)
                        db.session.commit()


@app.route('/')
def index():
    bot = telegram.Bot(token=app.config['TELEGRAM_BOT_TOKEN'])  # TODO: Use single bot instance
    bot_me = asyncio.run(bot.getMe())
    return (f'<a href="https://t.me/{bot_me.username}">{bot_me.first_name}</a> - a telegram bot for GitHub releases.'
            '<br><br>'
            'Source code available at <a href="https://github.com/JanisV/release-bot">release-bot</a>')


class TelegramThread(threading.Thread):
    def run(self) -> None:
        from telegram_bot import run_telegram_bot
        run_telegram_bot()


telegram_thread = TelegramThread()
telegram_thread.daemon = True


def run_app():
    scheduler.start()
    # telegram_thread.start()
