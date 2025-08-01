import glob
import os


class Kfake():
    def __init__(self):
        self.conn = 'fake'

    def volumes(self, iso=True, extended=False):
        return glob.glob('*.iso')

    def add_image(self, url, pool, cmds=[], name=None, size=None, convert=False):
        os.system("curl -Lk %s > %s" % (url, os.path.basename(url)))

    def delete_image(self, image, pool=None):
        os.remove(image)

    def get_pool_path(self, pool):
        return '.'

    def list(self):
        return [{}]
