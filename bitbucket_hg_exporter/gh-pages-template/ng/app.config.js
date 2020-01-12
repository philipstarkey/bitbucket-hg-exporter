'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      var wait_for_global_data = {
        init: ['InitService', function(Init) {
          return Init.promise;
        }]
      }

      $routeProvider.
        when('/', {
          template: '<repo-list></repo-list>',
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project', {
          template: '<index-page></index-page>',
          resolve: wait_for_global_data,
        }).
        // Issues list
        when('/:owner/:project/issues', {
          redirectTo: '/:owner/:project/issues/page/1'
        }).
        when('/:owner/:project/issues/page/:pageId', {
          template: '<issues-list></issues-list>'
        }).
        // Issue details
        when('/:owner/:project/issues/:issueId/page/:pageId', {
          template: '<issue-details></issue-details>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/issues/:issueId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issues/:issueId/:slug', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issue/:issueId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issue/:issueId/page/:pageId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/:pageId'
        }).
        when('/:owner/:project/issue/:issueId/:slug', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        // pull requests list
        when('/:owner/:project/pull-requests/page/:pageId', {
          template: '<pullrequests-list></pullrequests-list>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/pull-requests', {
          redirectTo: '/:owner/:project/pull-requests/page/1'
        }).
        // pull requests details
        when('/:owner/:project/pull-requests/:prId/page/:pageId', {
          template: '<pullrequest-details></pullrequest-details>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/pull-requests/:prId', {
          redirectTo: '/:owner/:project/pull-requests/:prId/page/1'
        }).
        when('/:owner/:project/pull-requests/:prId/:slug', {
          redirectTo: '/:owner/:project/pull-requests/:prId/page/1'
        }).
        // commit details
        when('/:owner/:project/commits/:commitSlug/page/:pageId', {
          template: '<commit-details></commit-details>',
          reloadOnSearch: false,
          resolve: wait_for_global_data,
        }).
        when('/:owner/:project/commits/:commitSlug', {
          redirectTo: '/:owner/:project/commits/:commitSlug/page/1'
        }).
        // source code redirect
        when('/:owner/:project/src/:commitSlug/:path*', {
          resolve: wait_for_global_data,
          // TODO: handle routeChangeError on malformed link
          resolveRedirectTo: function($rootScope, InitService, $location, $window, $route, $http){
            return InitService.promise.then(function(){
              var routeParams = $route.current.params;
              var project_slug = routeParams.owner + '/' + routeParams.project;
              
              // Get the Git hash from the hg hash
              if (routeParams.commitSlug == 'tip' || routeParams.commitSlug == 'default')
              {
                return 'master';
              }
              else
              {
                // TODO: If the commit slug is shortened, we may not have a file with that name...
                return $http.get($rootScope.projects[project_slug]['project_path']+'commit/'+routeParams.commitSlug+'.json');
              }
            }).then(function(response) {
              // build the URL for the github redirect
              var routeParams = $route.current.params;
              var project_slug = routeParams.owner + '/' + routeParams.project;
              // TODO: redirect to an error page if there is no github_repo
              var url = $rootScope.projects[project_slug]['github_repo']
              url += "/blob/" 
              
              // load the git hash from the queried data if necessary
              var git_hash = ''
              if (response == 'master')
              {
                git_hash = response
              }
              else
              {
                var commit_data = response.data;
                git_hash = commit_data['git_hash'];
              }

              // add the source code file path
              url += git_hash + "/" + routeParams.path

              // add the line number if necessary
              if ($location.hash() != '')
              {
                var lines = $location.hash().split('-');
                var line = lines[lines.length-1];
                url += "#L" + line;
              }

              // perform the redirect
              $window.location.href = url

            });
            
          }
        }).
        otherwise('/');
    }
  ]);