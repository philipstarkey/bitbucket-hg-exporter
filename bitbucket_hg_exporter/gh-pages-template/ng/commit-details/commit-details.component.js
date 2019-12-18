'use strict';

// Register `commitDetails` component, along with its associated controller and template
angular.
  module('commitDetails').
  component('commitDetails', {
    templateUrl: 'ng/commit-details/commit-details.template.html',
    controller: ['$http', '$routeParams', '$sce', '$rootScope', '$location', '$timeout', '$anchorScroll', function CommitDetailsController($http, $routeParams, $sce, $rootScope, $location, $timeout, $anchorScroll) {
        var self = this;

        //pagination info
        self.commitSlug = $routeParams.commitSlug;
        self.commentPage = $routeParams.pageId;
        self.project_slug = $routeParams.owner + '/' + $routeParams.project;
        self.mainHtml = "";
      
      
        $http.get($rootScope.projects[self.project_slug]['project_path']+'commit/'+self.commitSlug+'.json').then(function(response) {
            self.commit = response.data;
            self.mainHtml = $sce.trustAsHtml(self.commit['rendered']['message']['html']);
        });

        $http.get($rootScope.projects[self.project_slug]['project_path']+'commit/'+self.commitSlug+'/comments_page='+self.commentPage+'.json').then(function(response) {
          self.comments = response.data;
          angular.forEach(self.comments['values'], function(value, index){
            self.comments['values'][index]['content']['html'] = $sce.trustAsHtml(self.comments['values'][index]['content']['html']);

          });

          // Now that the comments are (about to be) loaded
          // scroll to the comment specified in the hash if there is one
          $timeout(function(){$anchorScroll($location.hash());});
      });
        
    }]
  });