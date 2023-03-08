import multiprocessing

bind = "0.0.0.0:80"
workers = multiprocessing.cpu_count() * 2 + 1

daemon = False
reload = False

accesslog = "/logs/access.log"
access_log_format = '%(h)s %(l)s %(u)s %(t)s %(M) "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'
errorlog = "/logs/error.log"