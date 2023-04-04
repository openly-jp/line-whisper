import multiprocessing

bind = "0.0.0.0:80"
workers = multiprocessing.cpu_count() * 2 + 1

daemon = False
reload = False

accesslog = "/logs/access.log"
errorlog = "/logs/error.log"