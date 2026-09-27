"""Abertura de conexões SQLite com suporte a leitura por nome de coluna."""
import sqlite3


def connect_database(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection