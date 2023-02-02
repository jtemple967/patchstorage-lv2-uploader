from typing import Optional, Union, Dict
import os
import shutil
import pathlib
import json
import requests
import click
import pathlib
from bundles import PatchstorageMultiTargetBundle, PluginFieldMissing, BundleBadContents, PluginBadContents


PS_API_URL = 'https://patchstorage.com/api/beta'
PS_LV2_PLATFORM_ID = 8046
PS_TAGS_DEFAULT = ['lv2-plugin', ]
PATH_ROOT = pathlib.Path(__file__).parent.resolve()
PATH_PLUGINS = PATH_ROOT / 'plugins'
PATH_DIST = PATH_ROOT / 'dist'

# for dev purposes
DEBUG = False

if DEBUG:
    PS_API_URL = 'http://localhost/api/beta'
    PS_LV2_PLATFORM_ID = 5027


class PatchstorageException(Exception):
    pass

# TODO: prepare requests and send using a separate staticmethod w/ exception handling
class Patchstorage:

    PS_API_TOKEN = None
    USER_AGENT = 'lv2-plugin-uploader'

    @staticmethod
    def decode_json_response(r: requests.Response) -> dict:
        resp_data: dict = {}
        
        try:
            resp_data = r.json()
        except requests.exceptions.JSONDecodeError as e:
            raise PatchstorageException(f'Failed to decode JSON response for {r.url}')

        return resp_data

    @staticmethod
    def auth(username: str, password: str) -> None:
        assert PS_API_URL is not None
        assert username
        assert password

        url = PS_API_URL + '/auth/token'

        click.echo(f'Authenticating: {username} ({url})')

        r = requests.post(url, data={
            'username': username,
            'password': password
        }, headers={'User-Agent': Patchstorage.USER_AGENT})

        resp_data = Patchstorage.decode_json_response(r)

        if not r.ok:
            raise PatchstorageException('Failed to authenticate')

        Patchstorage.PS_API_TOKEN = resp_data['token']

    @staticmethod
    def get_platform_targets(platform_id: int) -> list:
        assert PS_API_URL is not None

        url = f"{PS_API_URL}/platforms/{platform_id}"

        click.echo(f'Getting supported targets from {url}')

        r = requests.get(url, headers={'User-Agent': Patchstorage.USER_AGENT})

        resp_data = Patchstorage.decode_json_response(r)

        assert r.status_code == 200, r.content
        assert resp_data['targets'], f"Error: No targets field for platform {platform_id}"

        click.echo(f"Supported targets: {[t['slug'] for t in resp_data['targets']]}")

        return resp_data['targets']

    @staticmethod
    def upload_file(path: str, target_id: Optional[int] = None) -> str:
        assert isinstance(path, str)
        assert isinstance(target_id, int) or target_id is None

        if Patchstorage.PS_API_TOKEN is None:
            raise PatchstorageException('Not authenticated')

        click.echo(f'Uploading: {path}')

        post_data: dict = {}

        if target_id is not None:
            post_data['target'] = target_id

        r = requests.post(PS_API_URL + '/files', data=post_data, files={
            'file': open(path, 'rb')
        },
            headers={
            'Authorization': 'Bearer ' + Patchstorage.PS_API_TOKEN,
            'User-Agent': Patchstorage.USER_AGENT
        })

        resp_data = Patchstorage.decode_json_response(r)

        if not r.ok:
            raise PatchstorageException(
                f'Failed to upload file {path} {resp_data}')

        click.secho(f'Uploaded: {resp_data["filename"]} (ID:{resp_data["id"]})')

        return resp_data['id']

    @staticmethod
    def get(id: Optional[str] = None, uids: Optional[list] = None) -> Optional[dict]:
        if Patchstorage.PS_API_TOKEN is None:
            raise PatchstorageException('Not authenticated')

        if id is None and uids is None:
            raise PatchstorageException(
                'Internal error - must provide ID or UID')

        if id is not None:
            r = requests.get(PS_API_URL + '/patches/' + str(id),
                             headers={'User-Agent': Patchstorage.USER_AGENT})

            resp_data = Patchstorage.decode_json_response(r)

            if not r.ok:
                click.echo(r.status_code)
                click.echo(r.request)
                click.echo(resp_data)
                raise PatchstorageException(f'Failed to get plugin {str(id)}')

            if resp_data.get('id') == id:
                return resp_data
            
            raise PatchstorageException(f'Failed to get plugin {str(id)}')

        if uids is not None:

            params: Dict[str, Union[int, list]] = {
                'uids[]': uids,
                'platforms[]': PS_LV2_PLATFORM_ID
            }

            r = requests.get(PS_API_URL + '/patches/', params=params,
                             headers={'User-Agent': Patchstorage.USER_AGENT})

            resp_data = Patchstorage.decode_json_response(r)

            if not r.ok:
                click.echo(r.status_code)
                click.echo(r.request)
                click.echo(resp_data)
                raise PatchstorageException(
                    f'Failed to get plugin with uids {uids}')

            if isinstance(resp_data, list) and len(resp_data) > 0:
                if len(resp_data) > 1:
                    raise PatchstorageException(
                        f'Multiple plugins found with provided uids {uids}')
                
                r = requests.get(PS_API_URL + '/patches/' + str(resp_data[0]['id']),
                             headers={'User-Agent': Patchstorage.USER_AGENT})
                
                resp_data = Patchstorage.decode_json_response(r)

                return resp_data

        return None

    @staticmethod
    def upload(folder: str, data: dict) -> dict:
        assert 'artwork' in data, 'Missing artwork field in patchstorage.json'
        assert 'files' in data, 'Missing files field in patchstorage.json'

        if Patchstorage.PS_API_TOKEN is None:
            raise PatchstorageException('Not authenticated')

        artwork_id = Patchstorage.upload_file(data['artwork'])

        file_ids: list = []

        for file in data['files']:
            file_id = Patchstorage.upload_file(
                file['path'], target_id=file.get('target_id'))
            file_ids.append(int(file_id))

        data['artwork'] = int(artwork_id)
        data['files'] = file_ids

        click.echo(f'Uploading: {folder}')

        r = requests.post(PS_API_URL + '/patches', json=data, headers={
            'Authorization': 'Bearer ' + Patchstorage.PS_API_TOKEN,
            'User-Agent': Patchstorage.USER_AGENT
        })

        resp_data = Patchstorage.decode_json_response(r)

        if not r.ok:
            raise PatchstorageException(
                f'Failed to upload {folder} {resp_data}')

        return resp_data

    @staticmethod
    def update(folder: str, data: dict, id: int) -> dict:
        if Patchstorage.PS_API_TOKEN is None:
            raise PatchstorageException('Not authenticated')
        
        click.echo(f'Updating: {folder}')

        artwork_id = Patchstorage.upload_file(data['artwork'])

        file_ids: list = []

        for file in data['files']:
            file_id = Patchstorage.upload_file(
                file['path'], target_id=file.get('target_id'))
            file_ids.append(int(file_id))

        data['artwork'] = int(artwork_id)
        data['files'] = file_ids

        r = requests.put(PS_API_URL + '/patches/' + str(id), json=data, headers={
            'Authorization': 'Bearer ' + Patchstorage.PS_API_TOKEN
        })

        resp_data = Patchstorage.decode_json_response(r)

        if not r.ok:
            raise PatchstorageException(
                f'Failed to update {folder} {resp_data}')

        return resp_data

    @staticmethod
    def push(username: str, folder: str, auto: bool, force: bool) -> None:

        with open(os.path.join(PATH_DIST, folder, 'patchstorage.json'), 'r') as f:
            data = json.loads(f.read())

        if 'uids' not in data or len(data['uids']) == 0:
            raise PatchstorageException(
                f'Missing/bad uids field in patchstorage.json for {folder}')

        uploaded = Patchstorage.get(uids=data['uids'])

        # not uploaded or was removed from Patchstorage
        if uploaded is None:
            click.echo(f'Processing: {folder}')

            if auto:
                result = Patchstorage.upload(folder, data)

            elif not click.confirm(f'(?): Upload {folder} (local-{data["revision"]})?'):
                return

            else:
                result = Patchstorage.upload(folder, data)

        # uploaded already
        else:

            # check if uploaded by same user
            if uploaded['author']['slug'].lower() == username.lower():
                # click.echo(f'{folder} was previously uploaded by you')
                pass
            else:
                click.secho(
                    f'Skip: {folder} already uploaded by {uploaded["author"]["slug"]} ({uploaded["url"]})', fg='yellow')
                return

            # if force, re-upload
            if force:
                result = Patchstorage.update(folder, data, uploaded['id'])

            # if auto, upload only if revision is different or not same targets
            elif auto:
                if uploaded['revision'] == data['revision'] and len(uploaded['files']) == len(data['files']):
                    click.echo(f'Skip: {folder} same version & targets')
                    return

                result = Patchstorage.update(folder, data, uploaded['id'])

            elif not click.confirm(f'(?): Update {folder} (local-ver:{data["revision"]}, cloud-ver:{uploaded["revision"]}, local-targets:{len(data["files"])}, cloud-targets:{len(uploaded["files"])})?'):
                return

            else:
                result = Patchstorage.update(folder, data, uploaded['id'])

        click.secho(f'Published: {result["url"]} (ID:{result["id"]})', fg='green')


class PluginManagerException(Exception):
    pass


class PluginManager:

    def __init__(self) -> None:
        assert PATH_ROOT
        assert PATH_PLUGINS
        assert PATH_DIST
        assert PS_LV2_PLATFORM_ID

        self.plugins_path = pathlib.Path(PATH_PLUGINS)
        self.dist_path = pathlib.Path(PATH_DIST)
        self.targets = Patchstorage.get_platform_targets(PS_LV2_PLATFORM_ID)
        self.licenses = self.load_json_data('licenses.json')
        self.categories = self.load_json_data('categories.json')
        self.overwrites = self.load_json_data('plugins.json')
        self.multi_bundles_map: dict = {}
        self._context: Optional[dict] = None

    @staticmethod    
    def load_json_data(filename: str) -> dict:
        try:
            path = PATH_ROOT / filename
            with open(path, "r") as f:
                return json.loads(f.read())
        except FileNotFoundError:
            raise PluginManagerException(f'Missing {filename} file in {PATH_ROOT}')
        except json.decoder.JSONDecodeError:
            raise PluginManagerException(f'Invalid JSON data in {filename}')

    @staticmethod
    def do_cleanup(path: pathlib.Path) -> None:
        assert isinstance(path, pathlib.Path), f'Invalid path type: {path}'

        if path.exists():
            try:
                shutil.rmtree(path)
            except OSError:
                raise PluginManagerException(f'Failed to cleanup {path}')
        
        path.mkdir(parents=True, exist_ok=True)

    def scan_plugins_directory(self) -> dict:
        if not self.plugins_path.exists():
            raise PluginManagerException(f'Plugins directory not found: {PATH_PLUGINS}')
        
        click.echo(f"Supported targets: {[t['slug'] for t in self.targets]}")

        folders_found = [path for path in self.plugins_path.iterdir() if path.is_dir()]
        
        click.echo(f"Target folders found: {[str(f) for f in folders_found]}")

        candidates: dict = {}

        for t in self.targets:
            t_folder = self.plugins_path / t['slug']

            if not t_folder.exists():
                click.echo(f'Warning: No folder found for target \'{t["slug"]}\'')
                continue

            for p_path in t_folder.iterdir():

                if not p_path.is_dir():
                    continue
                
                p_folder = p_path.parts[-1]

                if p_folder not in candidates:
                    candidates[p_folder] = []

                candidates[p_folder].append({
                    'slug': t['slug'],
                    'id': t['id'],
                    'path': p_path
                })

        click.echo(f"Total candidates: {len(candidates)}")
        click.echo(f"Total candidates builds: {sum([len(candidates[p]) for p in candidates])}")

        for package_name, targets_info in candidates.items():
            multi_bundle = PatchstorageMultiTargetBundle(package_name, targets_info)

            try:
                multi_bundle.validate_basic_files()
            except (BundleBadContents, PluginFieldMissing) as e:
                msg = f'Error: {e}'
                click.secho(msg, fg='red')
                continue
    
            self.multi_bundles_map[package_name] = multi_bundle

        return self.multi_bundles_map
    
    def get_multi_bundle(self, package_name: str) -> PatchstorageMultiTargetBundle:
        if package_name not in self.multi_bundles_map:
            raise PluginManagerException(f'Bundle not found: {package_name}')
        return self.multi_bundles_map[package_name]
    
    def prepare_bundles(self) -> None:
        prepared = 0
        failed = 0

        for bundle in self.multi_bundles_map:
            ok = self.prepare_bundle(self.multi_bundles_map[bundle])
            if ok:
                prepared += 1
            else:
                failed += 1
        
        click.secho(f'Prepared: {prepared}', fg='green')
        click.secho(f'Failed: {failed}', fg='red')

    def prepare_bundle(self, multi_bundle: PatchstorageMultiTargetBundle) -> bool:
        try:
            self._prepare_bundle(multi_bundle)
            return True
        except (BundleBadContents, PluginFieldMissing) as e:
            msg = f'Error: {e}'
            click.secho(msg, fg='red')
            return False

    def _prepare_bundle(self, multi_bundle: PatchstorageMultiTargetBundle) -> None:
        package_name = multi_bundle.package_name
        path_plugins_dist = self.dist_path / package_name
        path_ps_json = path_plugins_dist / 'patchstorage.json'
        path_data_json = path_plugins_dist / 'debug.json'
        path_screenshot = path_plugins_dist / 'artwork.png'

        click.echo(f'Processing: {multi_bundle.package_name}')

        multi_bundle.validate()

        bundle = multi_bundle.bundles[0]

        # patchstorage field validation happens here
        patchstorage_data = bundle.get_patchstorage_data(
            platform_id=PS_LV2_PLATFORM_ID,
            licenses_map=self.licenses,
            categories_map=self.categories,
            overwrites=self.overwrites,
            default_tags=PS_TAGS_DEFAULT
        )

        self.do_cleanup(path_plugins_dist)

        if DEBUG:
            debug_path = bundle.create_debug_json(path_data_json)
            click.echo(f'Debug: {debug_path}')
        
        artwork_path = bundle.create_artwork(path_screenshot)
        click.echo(f'Created: {artwork_path}')

        tars_info = multi_bundle.create_tarballs(path_plugins_dist)
        click.echo(f'Created: {tars_info}')

        patchstorage_data['artwork'] = str(artwork_path)
        patchstorage_data['files'] = tars_info

        with open(path_ps_json, 'w', encoding='utf8') as f:
            f.write(json.dumps(patchstorage_data, indent=4))

        click.echo(f'Created: {path_ps_json}')
        click.secho(f'Prepared: {path_plugins_dist}', fg='green')

    @staticmethod
    def push_bundles(plugin_name: str, username: str, password: str, auto: bool, force: bool) -> None:
        Patchstorage.auth(username, password)

        if plugin_name != '':
            plugin_folder = PATH_DIST / plugin_name

            if not plugin_folder.exists():
                raise Exception(f'Plugin {plugin_name} not found or not prepared')

            plugins_folders = [str(plugin_folder)]
        else:
            plugins_folders = os.listdir(PATH_DIST)

        for folder in plugins_folders:
            try:
                Patchstorage.push(username, folder, auto, force)
            except PatchstorageException as e:
                click.secho(f'Error: {e}', fg='red')
                continue


@click.group()
def cli() -> None:
    """Very basic utility for publishing LV2 plugins to Patchstorage.com"""
    pass


@cli.command()
@click.argument('plugin_name', type=str, required=True)
def prepare(plugin_name: str) -> None:
    """Prepare *.tar.gz and patchstorage.json files"""

    manager = PluginManager()
    manager.scan_plugins_directory()
    manager.do_cleanup(PATH_DIST)

    if plugin_name == 'all':
        manager.prepare_bundles()
    else:
        multi_bundle = manager.get_multi_bundle(plugin_name)
        manager.prepare_bundle(multi_bundle)


@cli.command()
@click.argument('plugin_name', type=str, required=True)
@click.option('--username', required=True, type=str, help='Patchstorage Username')
@click.password_option(help='Patchstorage Password', confirmation_prompt=False)
@click.option('--auto', is_flag=True, default=False)
@click.option('--force', is_flag=True, default=False)
def push(plugin_name: str, username: str, password: str, auto: bool, force: bool) -> None:
    """Publish plugins to Patchstorage"""

    if plugin_name == 'all':
        plugin_name = ''

    manager = PluginManager()
    manager.push_bundles(plugin_name, username, password, auto, force)


if __name__ == '__main__':

    try:
        cli()
    except (click.Abort, PluginManagerException) as e:
        click.secho(f'Error: {str(e)}', fg='red')
    except PatchstorageException as e:
        click.secho(f'Patchstorage Error: {str(e)}', fg='red')
    # TODO: handle this inside Patchstorage class
    except requests.exceptions.ConnectionError as e:
        click.secho(f'Patchstorage Error: {str(e)}', fg='red')
