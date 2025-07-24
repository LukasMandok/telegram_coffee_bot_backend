from typing import Any
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from src.common.log import log_api_request, log_user_login_failed, log_auth_route_bypassed, log_auth_token_received

# NOTE: This one is not used
class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
            self,
            request: Request,
            call_next: RequestResponseEndpoint
        ) -> Response:
        
        log_api_request(request.scope['path'], request.method)
        
        ### ISSUE: This is anoying and not flexible enough
        if (request.scope['path'] in ['/users/login', '/docs', '/openapi.json']):
        # or request.scope['path'].startswith('/docs')):
            
            log_auth_route_bypassed(request.scope['path'])
            return await call_next(request)
        
        if request.scope['path'] != '/':
            required_headers = ['token']
            headers = [True for x in required_headers if x in request.headers]
            
            if len(headers) < len(required_headers):
                log_user_login_failed(0, f"Missing required headers: {required_headers}")
                return JSONResponse(
                    status_code=409,
                    content={'error': f'Header {required_headers} is missing'}
                )
            token = request.headers['token']
            # TODO: validate token

            log_auth_token_received()
        response = await call_next(request)
        return response 
    
    
#---------------------------------------------------------------------------------------------------
#  todo                                        TODO
#    
#  Create a middleware that handles access to the databases correctly.  
#  This means it should get and update everything from the local database
#  and updates the information later on to the gsheet asynchronously 
#
#---------------------------------------------------------------------------------------------------