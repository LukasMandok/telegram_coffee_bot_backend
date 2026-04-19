from typing import Any
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from src.common.log import Logger


logger = Logger("SecurityMiddleware")

# NOTE: This one is not used
class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
            self,
            request: Request,
            call_next: RequestResponseEndpoint
        ) -> Response:

        logger.trace(f"request {request.method} {request.scope['path']}", extra_tag="API")
        
        ### ISSUE: This is anoying and not flexible enough
        if (request.scope['path'] in ['/users/login', '/docs', '/openapi.json']):
        # or request.scope['path'].startswith('/docs')):

            logger.debug(f"auth_bypassed (route={request.scope['path']})", extra_tag="AUTH")
            return await call_next(request)
        
        if request.scope['path'] != '/':
            required_headers = ['token']
            headers = [True for x in required_headers if x in request.headers]
            
            if len(headers) < len(required_headers):
                logger.warning(f"auth_failed (missing_headers={required_headers})", extra_tag="AUTH")
                return JSONResponse(
                    status_code=409,
                    content={'error': f'Header {required_headers} is missing'}
                )
            token = request.headers['token']
            # TODO: validate token

            logger.trace("auth_token_received", extra_tag="AUTH")
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