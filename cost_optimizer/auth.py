import boto3
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from loguru import logger
from typing import Any, Dict, Optional

# Configure Loguru logging
logger.remove()
logger.add(sys.stderr, level=os.getenv('FASTMCP_LOG_LEVEL', 'WARNING'))

# Global client cache with thread safety
_cost_explorer_clients = {}
_sessions = {}
_client_roles = {}
_token_expiration = {}
_client_lock = threading.RLock()  # Reentrant lock for client operations

# Clients config cache with file monitoring and thread safety
_clients_config_cache = {}
_clients_config_path = None
_clients_config_mtime = None
_config_lock = threading.Lock()

# In-flight token refresh requests (deduplication)
_refresh_in_flight = {}  # Maps client_id to threading.Event for deduplication
_refresh_lock = threading.Lock()

# LRU cache for session management (max 300 sessions)
_MAX_CACHED_SESSIONS = 300
_session_access_times = OrderedDict()  # Tracks access times for LRU eviction

# Buffer time before token expiration to trigger refresh
TOKEN_REFRESH_BUFFER_SECONDS = 300

# Timeout for waiting on in-flight refresh (seconds)
# STS assume_role is typically fast, but network variance can add latency
REFRESH_WAIT_TIMEOUT = 45


def _get_clients_config_path() -> Optional[str]:
    """Get the clients.json config path, checking multiple locations."""
    config_path = os.getenv('CLIENTS_CONFIG_PATH')
    if config_path and os.path.exists(config_path):
        return config_path
    
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'clients.json')
    if os.path.exists(config_path):
        return config_path
    
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'clients.json')
    if os.path.exists(config_path):
        return config_path
    
    return None


def _load_clients_config() -> Dict[str, Dict[str, str]]:
    """Load client configuration with mtime-based caching and thread safety."""
    global _clients_config_cache, _clients_config_path, _clients_config_mtime
    
    with _config_lock:
        if _clients_config_path is None:
            _clients_config_path = _get_clients_config_path()
            if _clients_config_path is None:
                logger.warning('clients.json not found')
                return {}
        
        try:
            if not os.path.exists(_clients_config_path):
                logger.warning(f'clients.json disappeared: {_clients_config_path}')
                _clients_config_cache = {}
                _clients_config_mtime = None
                return {}
            
            current_mtime = os.path.getmtime(_clients_config_path)
            
            if _clients_config_mtime is not None and current_mtime == _clients_config_mtime:
                return _clients_config_cache
            
            logger.debug(f'Loading clients configuration from {_clients_config_path}')
            with open(_clients_config_path, 'r') as f:
                config = json.load(f)
            
            _clients_config_cache = config.get('clients', {})
            _clients_config_mtime = current_mtime
            
            logger.debug(f'Loaded {len(_clients_config_cache)} clients')
            return _clients_config_cache
                
        except json.JSONDecodeError as e:
            logger.error(f'Invalid JSON in clients.json: {e}')
            return {}
        except Exception as e:
            logger.error(f'Error loading clients.json: {e}')
            return {}


def _get_role_arn_for_client(client_id: str) -> Optional[str]:
    """Resolve role_arn for client_id from clients.json."""
    clients_config = _load_clients_config()
    if client_id in clients_config:
        return clients_config[client_id].get('role_arn')
    
    logger.warning(f'Client {client_id} not found in clients.json')
    return None


def _is_token_expired(client_id: str) -> bool:
    """Check if token has expired or is about to expire.
    NOTE: Caller must hold _client_lock.
    """
    if client_id not in _token_expiration:
        return True

    expiration_time = _token_expiration[client_id]
    now = datetime.now(timezone.utc)

    if now >= expiration_time - timedelta(seconds=TOKEN_REFRESH_BUFFER_SECONDS):
        logger.debug(f'Token for {client_id[:8]} expired or expiring soon')
        return True

    return False


def _evict_lru_session() -> None:
    """Evict least recently used sessions until within cache limit.
    NOTE: Caller must hold _client_lock.
    """
    while len(_session_access_times) > _MAX_CACHED_SESSIONS:
        oldest_client_id = next(iter(_session_access_times))

        logger.info(f'Evicting LRU session: {oldest_client_id[:8]} due to cache limit')
        _cost_explorer_clients.pop(oldest_client_id, None)
        _client_roles.pop(oldest_client_id, None)
        _token_expiration.pop(oldest_client_id, None)
        _sessions.pop(oldest_client_id, None)
        _session_access_times.pop(oldest_client_id, None)



def _update_session_access_time_unsafe(client_id: str) -> None:
    """Update session access time for LRU tracking.
    
    IMPORTANT: Caller must already hold _client_lock. This is unsafe and requires
    external lock protection.
    """
    if client_id in _session_access_times:
        # Move to end (most recently used)
        _session_access_times.move_to_end(client_id)
    else:
        _session_access_times[client_id] = datetime.now(timezone.utc)


def _wait_for_in_flight_refresh(client_id: str) -> bool:
    """Wait for an in-flight token refresh to complete (deduplication).
    
    Returns True if refresh completed successfully, 
    Raises RuntimeError if refresh is stuck (timeout).
    """
    refresh_event = None
    
    with _refresh_lock:
        if client_id in _refresh_in_flight:
            refresh_event = _refresh_in_flight[client_id]
    
    if refresh_event:
        logger.debug(f'Waiting for in-flight refresh for {client_id[:8]}')
        # Wait for the refresh to complete (with timeout)
        is_set = refresh_event.wait(timeout=REFRESH_WAIT_TIMEOUT)
        
        if not is_set:
            logger.error(f'Refresh timeout for {client_id[:8]} after {REFRESH_WAIT_TIMEOUT}s')
            raise RuntimeError(
                f'Token refresh in progress for {client_id[:8]} is taking too long. '
                f'Please retry in a moment or manually close the session.'
            )
        
        logger.debug(f'In-flight refresh completed for {client_id[:8]}')
        return True
    
    return False


def _mark_refresh_in_flight(client_id: str) -> Optional[threading.Event]:
    """Mark a client refresh as in-flight (returns event to signal completion).
    
    Returns threading.Event if this thread should perform the refresh,
    None if another thread is already refreshing.
    """
    with _refresh_lock:
        if client_id in _refresh_in_flight:
            # Another thread is already refreshing
            return None
        
        event = threading.Event()
        _refresh_in_flight[client_id] = event
        return event


def _clear_refresh_in_flight(client_id: str, event: threading.Event) -> None:
    """Signal completion of in-flight refresh and remove tracking."""
    event.set()
    
    with _refresh_lock:
        _refresh_in_flight.pop(client_id, None)


def get_cost_explorer_client(client_id: str) -> Any:
    """Get or create Cost Explorer client with automatic token refresh and thread safety."""
    role_arn = _get_role_arn_for_client(client_id)
    if not role_arn:
        logger.error(f'Could not resolve role_arn for {client_id}')
        raise ValueError(f'Client {client_id} not found in clients.json')
    
    # Check if another thread is already refreshing this client
    try:
        if _wait_for_in_flight_refresh(client_id):
            # Refresh completed by another thread, try to use cached client
            with _client_lock:
                if client_id in _cost_explorer_clients:
                    cached_role = _client_roles.get(client_id)
                    
                    if cached_role == role_arn and not _is_token_expired(client_id):
                        logger.debug(f'Using cached client for {client_id[:8]} after in-flight refresh')
                        _update_session_access_time_unsafe(client_id)
                        return _cost_explorer_clients[client_id]
    except RuntimeError as e:
        logger.warning(f'Refresh timeout: {e}. Attempting own refresh.')
        # Fall through to attempt own refresh
    
    # Check cached client with role and token validation
    with _client_lock:
        if client_id in _cost_explorer_clients:
            cached_role = _client_roles.get(client_id)
            
            if cached_role == role_arn and not _is_token_expired(client_id):
                logger.debug(f'Using cached client for {client_id[:8]}')
                _update_session_access_time_unsafe(client_id)
                return _cost_explorer_clients[client_id]
            
            # Token expired or role mismatch - need to refresh
            if cached_role != role_arn:
                logger.info(f'Role mismatch for {client_id[:8]}, will refresh')
            else:
                logger.info(f'Token expired for {client_id[:8]}, will refresh')
    
    # Attempt to acquire refresh lock for this client
    refresh_event = _mark_refresh_in_flight(client_id)
    
    if refresh_event is None:
        # Another thread is already refreshing, wait for it
        logger.debug(f'Another thread is refreshing {client_id[:8]}, waiting...')
        try:
            _wait_for_in_flight_refresh(client_id)
        except RuntimeError as e:
            logger.warning(f'Refresh timeout while waiting: {e}. Attempting own refresh.')
            pass
        
        # After waiting, check if client is now cached
        with _client_lock:
            if client_id in _cost_explorer_clients:
                cached_role = _client_roles.get(client_id)
                if cached_role == role_arn and not _is_token_expired(client_id):
                    logger.debug(f'Using refreshed client for {client_id[:8]}')
                    _update_session_access_time_unsafe(client_id)
                    return _cost_explorer_clients[client_id]
        
        # Client still not available after wait - shouldn't happen
        logger.error(f'Client still not available after refresh wait for {client_id[:8]}')
        raise RuntimeError(f'Failed to obtain client for {client_id[:8]} after waiting for refresh')

    try:
        aws_region = os.environ.get('AWS_REGION', 'us-east-1')
        
        logger.debug('Using IAM Anywhere credentials')
        session = boto3.Session(region_name=aws_region)
        
        safe_id = ''.join(c if c.isalnum() else '-' for c in client_id)[:12]
        session_name = f'ce-{safe_id}-{int(time.time())}'
        logger.debug(f'Assuming role {role_arn} with session name {session_name}')
        
        sts_client = session.client('sts')
        assumed_role = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name
        )
        
        credentials = assumed_role['Credentials']
        client = boto3.Session(
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken'],
            region_name=aws_region
        ).client('ce')
        
        logger.info(f'Created client with role {role_arn} for {client_id[:8]}')
        
        # Atomically update all caches (single lock acquisition)
        with _client_lock:
            # Set token expiration (no internal lock needed)
            if credentials['Expiration'].tzinfo is None:
                expiration_utc = credentials['Expiration'].replace(tzinfo=timezone.utc)
            else:
                expiration_utc = credentials['Expiration'].astimezone(timezone.utc)
            
            _token_expiration[client_id] = expiration_utc
            logger.debug(f'Set token expiration for {client_id[:8]} to {expiration_utc.isoformat()}')
            
            # Update client and role caches
            _cost_explorer_clients[client_id] = client
            _client_roles[client_id] = role_arn
            
            # Update session info
            now = datetime.now(timezone.utc)
            if client_id not in _sessions:
                _sessions[client_id] = {
                    'id': client_id,
                    'role_arn': role_arn,
                    'created_at': now,
                    'last_accessed': now,
                }
            else:
                _sessions[client_id]['last_accessed'] = now
                _sessions[client_id]['role_arn'] = role_arn
            
            # Update LRU tracking and evict if needed
            _update_session_access_time_unsafe(client_id)
            _evict_lru_session()
        
        return client
        
    except Exception as e:
        logger.error(f'Error creating Cost Explorer client: {e}')
        raise
    finally:
        # Signal completion of refresh if we acquired the lock
        if refresh_event is not None:
            _clear_refresh_in_flight(client_id, refresh_event)


def get_account_id(client_id: str) -> Optional[str]:
    """Get the account_id for a client from clients.json.

    Returns the explicit ``account_id`` field if present, otherwise extracts
    it from the ``role_arn``.  Returns ``None`` only when the client is not
    found at all.
    """
    clients_config = _load_clients_config()
    client_cfg = clients_config.get(client_id)
    if not client_cfg:
        return None

    # Prefer explicit account_id
    account_id = client_cfg.get('account_id')
    if account_id:
        return str(account_id)

    # Fallback: extract from role_arn  (arn:aws:iam::123456789012:role/name)
    role_arn = client_cfg.get('role_arn', '')
    parts = role_arn.split(':')
    if len(parts) >= 5 and parts[4]:
        return parts[4]

    return None


def get_account_type(client_id: str) -> str:
    """Get the account_type for a client from clients.json.

    Returns ``"payer"`` or ``"linked"``. Defaults to ``"linked"`` if not set.
    """
    clients_config = _load_clients_config()
    client_cfg = clients_config.get(client_id, {})
    return client_cfg.get('account_type', 'linked')


def is_payer_account(client_id: str) -> bool:
    """Check if client is a payer/management account."""
    return get_account_type(client_id) == 'payer'


def build_account_filter(
    client_id: str,
    account_scope: str = "auto",
    existing_filter: Optional[dict] = None,
) -> Optional[dict]:
    """Build a Cost Explorer filter scoped to the client's account.

    Filtering logic:
    - ``"auto"`` (default): applies LINKED_ACCOUNT filter only for payer accounts
      (to isolate their own costs). Linked accounts don't need filtering since
      CE already returns only their data when called with their own credentials.
    - ``"all"``: no filter — returns all accounts' data (only useful from payer).
    - ``"linked"``: always applies LINKED_ACCOUNT filter regardless of account type.

    Args:
        client_id: Client identifier from clients.json.
        account_scope: "auto", "all", or "linked".
        existing_filter: Optional existing filter to combine with.

    Returns:
        Combined filter dict, the original filter unchanged, or None if no
        filter is needed.
    """
    # "all" = no filtering, return everything (payer consolidated view)
    if account_scope == "all":
        return existing_filter

    # Determine if we need to filter
    needs_filter = False
    if account_scope == "linked":
        needs_filter = True
    elif account_scope == "auto":
        # Only payer accounts need filtering (linked accounts are already scoped)
        needs_filter = is_payer_account(client_id)

    if not needs_filter:
        return existing_filter

    account_id = get_account_id(client_id)
    if not account_id:
        return existing_filter

    account_filter = {
        'Dimensions': {
            'Key': 'LINKED_ACCOUNT',
            'Values': [account_id],
        }
    }

    if not existing_filter:
        return account_filter

    # Combine: if existing already has And, append; otherwise wrap both
    if 'And' in existing_filter:
        return {'And': existing_filter['And'] + [account_filter]}

    return {'And': [existing_filter, account_filter]}


def close_client_session(client_id: str) -> Dict[str, Any]:
    """Close client session and clean up resources (thread-safe)."""
    try:
        with _client_lock:
            _cost_explorer_clients.pop(client_id, None)
            _client_roles.pop(client_id, None)
            _token_expiration.pop(client_id, None)
            _sessions.pop(client_id, None)
            _session_access_times.pop(client_id, None)
        
        logger.info(f'Closed session: {client_id}')
        return {'status': 'success', 'message': f'Session {client_id} closed'}
    except Exception as e:
        logger.error(f'Error closing session {client_id}: {e}')
        return {'error': str(e)}


def get_active_sessions() -> Dict[str, Any]:
    """Get information about all active sessions (thread-safe)."""
    now = datetime.now(timezone.utc)
    sessions_list = []
    
    with _client_lock:
        for session_id, session_info in _sessions.items():
            token_expiry = _token_expiration.get(session_id)
            is_expired = False
            time_until_expiry = None
            
            if token_expiry:
                is_expired = now >= token_expiry - timedelta(seconds=TOKEN_REFRESH_BUFFER_SECONDS)
                time_until_expiry = (token_expiry - now).total_seconds()
            
            sessions_list.append({
                'session_id': session_id,
                'role_arn': session_info.get('role_arn'),
                'created_at': session_info['created_at'].isoformat(),
                'last_accessed': session_info.get('last_accessed', session_info['created_at']).isoformat(),
                'token_expires_at': token_expiry.isoformat() if token_expiry else None,
                'token_expired': is_expired,
                'seconds_until_expiry': round(time_until_expiry, 2) if time_until_expiry else None
            })
    
    return {
        'active_sessions': len(sessions_list),
        'sessions': sessions_list
    }