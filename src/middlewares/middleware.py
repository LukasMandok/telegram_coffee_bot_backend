from typing import Any, Coroutine
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# NOTE: This one is not used
class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
            self,
            request: Request,
            call_next: RequestResponseEndpoint
        ) -> Coroutine[Any, Any, Response]:
        
        print("request scope:", request.scope['path'])
        
        ### ISSUE: This is anoying and not flexible enough
        if (request.scope['path'] in ['/users/login', '/docs', '/openapi.json']):
        # or request.scope['path'].startswith('/docs')):
            
            print("SecurityMiddleware - this route does not require a token")
            return await call_next(request)
        
        if request.scope['path'] != '/':
            required_headers = ['token']
            headers = [True for x in required_headers if x in request.headers]
            
            if len(headers) < len(required_headers):
                return JSONResponse(
                    status_code=409,
                    content={'error': f'Header {required_headers} is missing'}
                )
            token = request.headers['token']
            # TODO: validate token

            print("SecurityMiddleware - this is the token received:", token)
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